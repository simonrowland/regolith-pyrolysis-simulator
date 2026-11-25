"""Provenance of an engine-input composition, and the SC-458 / d-323 / d-325 rules.

migrate.py writes the token from locators, notes, and table identity. It does
not compare a component amount to the scored point. score.py refuses from the
token. Both call this module. This module does not import score or migrate.

Copies left elsewhere, on purpose:
- feedstock_composition.FEOT_FROM_FE2O3 is 2*71.844/159.687. The d-323 input
  construction uses the ruling's rounded masses, 2*M_FeO/M_Fe2O3 = 143.69/159.69.
- catalogue proxies stay provenance-None. The echo guard does not invent a token.
- element_wt_percent rows are a different bulk and are not stamped here.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

from simulator.battery.enums import (
    NoticeKind,
    PhaseRole,
    Quantity,
    RefusalReason,
)
from simulator.battery.identity import Identity, quantity_token
from simulator.battery.records import Composition, Experiment, Locator, Notice, Observation
from simulator.feedstock_composition import _TOTAL_IRON_AS_FEO_KEYS

SCORED_PHASE_ANALYSIS = "scored_phase_analysis"
PRINTED_STARTING_BULK = "printed_starting_bulk"
NAMED_MATERIAL_MIXTURE = "named_material_mixture_of_printed_starts"
ANALYSED_MELT_MINUS_SCORED = "analysed_melt_minus_scored"
SAME_RUN_INITIAL_ANALYSED_MELT = "same_run_initial_analysed_melt"
COMPOSITION_FROM_SINGLE_LIQUID_GLASS = "composition_from_single_liquid_glass"
SINGLE_LIQUID_GLASS_REDOX_FEOT_ONLY = "redox input uses FeOt total only"

INPUT_PROVENANCE_TOKENS = frozenset(
    {
        SCORED_PHASE_ANALYSIS,
        PRINTED_STARTING_BULK,
        NAMED_MATERIAL_MIXTURE,
        ANALYSED_MELT_MINUS_SCORED,
        SAME_RUN_INITIAL_ANALYSED_MELT,
        COMPOSITION_FROM_SINGLE_LIQUID_GLASS,
    }
)

# Melt observables that are not input components (d-323). A major oxide is
# not in this set. Trace elements outside this list stay on the echo refusal.
_NON_INPUT_MELT_FORMULAS = frozenset({"S", "Cl", "H2O", "CO2", "H", "C"})
_REDOX_QUANTITIES = frozenset({Quantity.FE3_FE2_RATIO, Quantity.VALENCE_RATIO})
# A saturation-defined quantity. The saturating phase is the definition, so
# crystalline text does not refuse it. Dissolved S as a phase composition
# stays on the non-input formula list and still needs printed single-melt
# evidence.
_SATURATION_QUANTITIES = frozenset(
    {Quantity.SULFIDE_CAPACITY, Quantity.CHLORIDE_CAPACITY}
)
_MELT_ROLES = frozenset({PhaseRole.MELT, PhaseRole.SLAG, PhaseRole.GLASS})
_PRINTED_BULK_TOKENS = frozenset({PRINTED_STARTING_BULK, NAMED_MATERIAL_MIXTURE})

# d-325. 100% liquid is this mass fraction of the bulk, from the engine
# result only. Phase names are not the test.
SINGLE_LIQUID_MASS_FRACTION = 1.0 - 1e-6

# d-323 redox input. Two FeO formula units per Fe2O3.
# The ruling's rounded masses are M_FeO = 71.845 g/mol and
# M_Fe2O3 = 159.69 g/mol, so 2*M_FeO/M_Fe2O3 = 143.69/159.69.
# feedstock_composition.FEOT_FROM_FE2O3 is a different rounding and stays there.
_FEO_MASS_TIMES_TWO = Decimal("143.69")
_FE2O3_MASS = Decimal("159.69")

_STARTING_PHRASE = (
    "starting material",
    "starting composition",
    "starting charge",
)
# Word boundaries. "unbatched" and "nominally" are not a printed start.
_STARTING_WORD = re.compile(r"\b(?:batched|nominal)\b", re.IGNORECASE)
_ANALYSIS_NOTE = ("final slag", "product glass")
# "analysed" and "analyzed". analys[ez]ed matches neither spelling.
_ANALYSIS_WORD = re.compile(r"\banaly[sz]ed\b", re.IGNORECASE)
_QUENCH_WORD = re.compile(r"\bquench(?:ed|ing)?\b", re.IGNORECASE)
_GLASS_WORD = re.compile(r"\bglass\b", re.IGNORECASE)
# "whether ... glass is not stated" names glass only to deny it.
_GLASS_NOT_STATED = re.compile(
    r"whether\b.{0,80}?\bglass\b.{0,40}?\bnot stated\b",
    re.IGNORECASE | re.DOTALL,
)

_GLASS_ONLY = re.compile(
    r"crystal[-\s]+free|glass[-\s]+only|entirely liquid|no crystals|"
    r"remained entirely liquid",
    re.IGNORECASE,
)
# Quench crystals are not crystals at the run temperature. Both word orders.
_QUENCH_CRYSTAL = re.compile(
    r"quench(?:ed|ing)?[-\s]+crystals?|crystals?\s+formed\s+on\s+quench",
    re.IGNORECASE,
)
_ANALYZED_QUENCH_IS_LIQUID = re.compile(
    r"analy[sz]ed quench is the liquid",
    re.IGNORECASE,
)
_SAMPLED = re.compile(r"\bsampled\b", re.IGNORECASE)
_FROM_THE_LIQUID = re.compile(
    r"\bpt[-\s]+loop|platinum loop|from the liquid",
    re.IGNORECASE,
)
_CRYSTAL_BEARING = re.compile(
    r"\bcrystalline phases\b|\bolivine\b|\bplagioclase\b|\bclinopyroxene\b|"
    r"\bpyroxene\b|\bcrystals\b",
    re.IGNORECASE,
)
_SINGLE_LIQUID_CRYSTAL_BEARING = re.compile(
    r"\bspinel\b|\bwustite\b|\btridymite\b|\bcristobalite\b|"
    r"\bcrystal\b|\b(?:fe|iron) metal (?:precipitat\w*|droplets?)\b",
    re.IGNORECASE,
)
_ABOVE_LIQUIDUS = re.compile(
    r"above (?:the |its )?(?:dry )?liquidus",
    re.IGNORECASE,
)
_WORD_INITIAL = re.compile(r"(?<![A-Za-z])initial(?![A-Za-z])", re.IGNORECASE)
_WORD_FINAL = re.compile(r"(?<![A-Za-z])final(?![A-Za-z])", re.IGNORECASE)


def _is_crystal_bearing(
    text: str, *, include_single_liquid_phases: bool = False
) -> bool:
    """Shared crystal check; the migration rule also recognizes minor phases."""

    return _CRYSTAL_BEARING.search(text) is not None or (
        include_single_liquid_phases
        and _SINGLE_LIQUID_CRYSTAL_BEARING.search(text) is not None
    )


@dataclass(frozen=True)
class MeltEvidence:
    """Printed single-melt evidence. One class; crystals win over glass-only."""

    kind: str
    notice_reason: str | None = None
    quote: str | None = None

    @property
    def positive(self) -> bool:
        return self.kind.startswith("positive")

    @property
    def crystalline(self) -> bool:
        return self.kind == "crystalline"


@dataclass(frozen=True)
class InputDecision:
    """What the scorer does with an analysed-melt candidate. ignore = not d-323."""

    action: str
    refusal: RefusalReason | None = None
    detail: dict[str, object] | None = None
    evidence_reason: str | None = None
    quote: str | None = None


@dataclass(frozen=True)
class SinglePhaseBulkPolicy:
    """d-325 hook result. refusal set, or a notice, or None when case 1 applies."""

    refusal: RefusalReason | None = None
    notice_reason: str | None = None


@dataclass(frozen=True)
class SingleLiquidGlassCaveat:
    """Run caveat with its source locator, or an explicit not-stated value."""

    state: Literal["stated", "not_stated"]
    evidence: tuple[str, ...] = ()
    locator: Locator | None = None


@dataclass(frozen=True)
class SingleLiquidGlassDecision:
    """One source-grounded glass-composition decision for an observation."""

    run_id: str
    status: Literal["applied", "skipped", "not_target"]
    reason: str
    composition: Composition | None = None
    composition_wt_percent: tuple[tuple[str, Decimal], ...] = ()
    composition_locator: Locator | None = None
    glass_observation_ids: tuple[str, ...] = ()
    consumed_as_condition: bool = False
    volatile_loss: SingleLiquidGlassCaveat = SingleLiquidGlassCaveat("not_stated")
    container_exchange: SingleLiquidGlassCaveat = SingleLiquidGlassCaveat("not_stated")


_SINGLE_LIQUID_ASSEMBLAGE = re.compile(
    r"^(?:(?:one|single|only)\s+)?(?:l|liq\.?|liquid|melt|glass)"
    r"(?:\s*[/\-]\s*(?:l|liq\.?|liquid|melt|glass))?"
    r"(?:\s+only|\s+phase)?(?:\s+and\s+no\s+crystals?)?$",
    re.IGNORECASE,
)
_TWO_LIQUID_ASSEMBLAGE = re.compile(
    r"\b(?:two|2|multiple|immiscible)\s+(?:immiscible\s+)?liquids?\b|"
    r"\b(?:l|liq\.?|liquid)\s*[12]\b|\b(?:l|liq\.?|liquid)\s*(?:1\s*\+\s*2|1\s*,\s*2)\b",
    re.IGNORECASE,
)
_SOLID_ASSEMBLAGE = re.compile(r"\bsolid\b", re.IGNORECASE)
_POSITIVE_SINGLE_LIQUID_PHRASE = re.compile(
    r"\b(?:super[- ]?liquidus|fully molten|entirely molten|"
    r"quenched to glass|quenched glass)\b",
    re.IGNORECASE,
)
_NEGATED_SINGLE_LIQUID_PHRASE = re.compile(
    r"\b(?:not|never|wasn't|were not)\s+(?:fully molten|entirely molten|super[- ]?liquidus)\b",
    re.IGNORECASE,
)
_VOLATILE_LOSS_EVIDENCE = re.compile(
    r"\b(?:evaporat\w*|volatiliz\w*|volatilis\w*|open system|open crucible|"
    r"volatile loss|mass losses?|weight losses?|losses? of (?:alkali|iron|fe|"
    r"sulfur|sulphur|chlorine|chloride|cl)|(?:reports?|reported|measured|observed)"
    r"\s+(?:volatile\s+)?loss(?:es)?|(?:volatile\s+)?loss(?:es)?\s+"
    r"(?:was|were|is|are)?\s*(?:reported|measured|observed))\b",
    re.IGNORECASE,
)
_VOLATILE_COMPONENT_LOSS = re.compile(
    r"\b(?:alkali|na2o|k2o|iron|fe|sulfur|sulphur|so2|sulfide|sulphide|"
    r"chlorine|chloride|cl)\b.{0,40}\b(?:loss|lost|deplet\w*|evaporat\w*)\b|"
    r"\b(?:loss|lost|deplet\w*|evaporat\w*)\b.{0,40}\b(?:alkali|na2o|k2o|"
    r"iron|fe|sulfur|sulphur|so2|sulfide|sulphide|chlorine|chloride|cl)\b",
    re.IGNORECASE,
)
_CONTAINER_EXCHANGE_EVIDENCE = re.compile(
    r"\b(?:iron|fe)\b.{0,80}\b(?:loss|lost|absorbed|diffused|exchanged)\b.{0,80}\b(?:pt|platinum|crucible)\b|"
    r"\b(?:pt|platinum|crucible)\b.{0,80}\b(?:uptake|contaminat\w*|dissolv\w*|leach\w*|react\w*|attack\w*|corros\w*)\b|"
    r"\b(?:uptake|incorporat\w*|contaminat\w*|dissolv\w*|leach\w*)\b.{0,80}\b(?:pt|platinum|crucible)\b|"
    r"\bcrucible material uptake\b",
    re.IGNORECASE,
)
_SINGLE_LIQUID_PHASES = frozenset({"l", "liq", "liquid", "melt", "glass"})
_SINGLE_LIQUID_SAMPLE_ID_ALIASES = (
    ("sample_id", "sample_id"),
    ("sample_id_as_printed", "sample_id"),
    ("sample_id_as_published", "sample_id"),
    ("sample_no", "sample_no"),
    ("sample_no_as_printed", "sample_no"),
    ("sample_no_as_published", "sample_no"),
    ("sample", "sample"),
    ("sample_as_printed", "sample"),
    ("sample_as_published", "sample"),
)
_SINGLE_LIQUID_COMPONENT_MARKERS = (
    (
        re.compile(r"\b(?:na2o|k2o|alkali|sodium|potassium)\b", re.IGNORECASE),
        "alkali",
    ),
    (re.compile(r"\b(?:feo|fe2o3|feot|iron|fe)\b", re.IGNORECASE), "Fe"),
    (
        re.compile(r"\b(?:sulfur|sulphur|so2|sulfide|sulphide)\b", re.IGNORECASE),
        "S",
    ),
    (re.compile(r"(?<!\w)S(?!\w)"), "S"),
    (re.compile(r"\b(?:cl|chlorine|chloride)\b", re.IGNORECASE), "Cl"),
)


def _single_liquid_row_values(row: Mapping[str, object]) -> Mapping[str, object]:
    values = row.get("values")
    return values if isinstance(values, Mapping) else {}


def _single_liquid_identifier(row: Mapping[str, object]) -> tuple[str, str] | None:
    """An exact printed run/sample identifier; no name or locator heuristics."""

    experiment = _single_liquid_text(row.get("experiment"))
    if experiment is not None:
        return "experiment", experiment
    values = _single_liquid_row_values(row)
    for key in ("experiment_id", "run_id"):
        value = _single_liquid_text(row.get(key, values.get(key)))
        if value is not None:
            return key, value
    for key, canonical_key in _SINGLE_LIQUID_SAMPLE_ID_ALIASES:
        value = _single_liquid_text(row.get(key, values.get(key)))
        if value is not None:
            return canonical_key, value
    return None


def _single_liquid_sample_ids(row: Mapping[str, object]) -> dict[str, set[str]]:
    values = _single_liquid_row_values(row)
    sample_ids: dict[str, set[str]] = {}
    for container in (row, values, row.get("sample"), values.get("sample")):
        if not isinstance(container, Mapping):
            continue
        for key, canonical_key in _SINGLE_LIQUID_SAMPLE_ID_ALIASES:
            value = _single_liquid_text(container.get(key))
            if value is not None:
                sample_ids.setdefault(canonical_key, set()).add(value)
    return sample_ids


def _single_liquid_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, Mapping):
        raw_value = value.get("value")
        if isinstance(raw_value, str) and raw_value.strip():
            return raw_value.strip()
        if isinstance(raw_value, (int, float, Decimal)) and not isinstance(
            raw_value, bool
        ):
            return str(raw_value)
        state = value.get("state")
        if isinstance(state, Mapping) and str(state.get("tag") or "") == "value":
            raw = state.get("value")
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
            if isinstance(raw, (int, float, Decimal)) and not isinstance(raw, bool):
                return str(raw)
    return None


def _single_liquid_field_texts(
    row: Mapping[str, object], locator_for: Callable[[object], Locator | None]
) -> tuple[tuple[str, Locator | None], ...]:
    values = _single_liquid_row_values(row)
    fields = (
        "phase_assemblage_as_printed",
        "assemblage_as_printed",
        "phase_assemblage",
        "coexisting_phases",
        "crystals_as_published",
        "phase_as_printed",
        "phase_as_measured",
        "phase_as_analyzed",
        "phase_as_analysed",
        "phase",
        "phase_label",
        "phase_role",
        "printed_phase",
        "phase_note",
        "phase_assemblage_note",
        "printed_reading_note",
        "reading_note",
        "superliquidus_statement",
        "quench_statement",
        "quench_result",
        "post_quench_as_published",
        "sample_state",
        "sample_state_note",
        "characterization",
        "pretreatment",
        "form",
        "container",
        "method",
        "notes",
        "source_note",
        "comments_as_published",
        "comment",
        "table_title_as_published",
        "description",
        "statement",
        "run_description",
        "phase_description",
        "phases_as_published",
        "phase_list_as_published",
        "evaporation",
        "volatile_loss",
        "mass_loss",
        "weight_loss",
        "crucible",
        "container_material",
        "open_system_statement",
        "volatile_loss_statement",
        "container_exchange_statement",
    )
    found: list[tuple[str, Locator | None]] = []
    containers = [row, values]
    for parent in (row, values):
        for nested_key in ("sample", "conditions", "point_conditions"):
            nested = parent.get(nested_key)
            if isinstance(nested, Mapping):
                containers.append(nested)
    for container in containers:
        for field_name in fields:
            text = _single_liquid_text(container.get(field_name))
            if text is not None:
                raw = container.get(field_name)
                found.append((text, locator_for(raw) or locator_for(row)))
    return tuple(found)


def _single_liquid_phase_record(row: Mapping[str, object]) -> str | None:
    values = _single_liquid_row_values(row)
    quantity = str(values.get("quantity") or row.get("type") or "")
    if quantity != Quantity.EQUILIBRIUM_PHASE_COMPOSITION.value:
        return None
    for container in (row, values):
        for field_name in (
            "phase",
            "phase_as_printed",
            "phase_as_measured",
            "phase_label",
            "phase_role",
        ):
            text = _single_liquid_text(container.get(field_name))
            if text is not None:
                return text.strip().casefold().rstrip(".")
    return None


def _single_liquid_reason(
    rows: Sequence[Mapping[str, object]],
    experiment: Mapping[str, object] | None,
    locator_for: Callable[[object], Locator | None],
) -> str | None:
    evidence_rows = (() if experiment is None else (experiment,)) + tuple(rows)
    texts = tuple(
        item
        for row in evidence_rows
        for item in _single_liquid_field_texts(row, locator_for)
    )
    assemblage_fields = (
        "phase_assemblage_as_printed",
        "assemblage_as_printed",
        "phase_assemblage",
        "coexisting_phases",
    )
    assemblages = tuple(
        text
        for row in evidence_rows
        for container in (row, _single_liquid_row_values(row))
        for key in assemblage_fields
        for text in (_single_liquid_text(container.get(key)),)
        if text is not None
    )
    phase_condition_texts = tuple(
        text
        for row in evidence_rows
        for container in (row, _single_liquid_row_values(row))
        for point_conditions in (container.get("point_conditions"),)
        if isinstance(point_conditions, Mapping)
        for text in (_single_liquid_text(point_conditions.get("phase_assemblage")),)
        if text is not None
    )
    assemblages = (*assemblages, *phase_condition_texts)
    two_liquid = any(_TWO_LIQUID_ASSEMBLAGE.search(text) for text in assemblages)
    crystal_absence = re.compile(
        r"\b(?:no crystals?|crystal[- ]free|crystals? absent|crystals? not present|"
        r"crystals? not observed)\b",
        re.IGNORECASE,
    )
    crystals = any(
        (
            _SOLID_ASSEMBLAGE.search(_QUENCH_CRYSTAL.sub(" ", text))
            or _is_crystal_bearing(
                _QUENCH_CRYSTAL.sub(" ", text), include_single_liquid_phases=True
            )
        )
        and not crystal_absence.search(text)
        for text in assemblages
    ) or any(
        _is_crystal_bearing(
            _QUENCH_CRYSTAL.sub(" ", text), include_single_liquid_phases=True
        )
        and not crystal_absence.search(text)
        for text, _locator in texts
    ) or any(
        cleaned.strip().casefold()
        not in {"", "none", "no", "not present", "not observed", "n/a", "-"}
        and not crystal_absence.search(cleaned)
        for row in evidence_rows
        for container in (row, _single_liquid_row_values(row))
        for raw_text in (container.get("crystals_as_published"),)
        if (text := _single_liquid_text(raw_text)) is not None
        for cleaned in (_QUENCH_CRYSTAL.sub(" ", text),)
    )
    negated = any(_NEGATED_SINGLE_LIQUID_PHRASE.search(text) for text, _ in texts)
    phrase_positive = any(
        _POSITIVE_SINGLE_LIQUID_PHRASE.search(text)
        and not _NEGATED_SINGLE_LIQUID_PHRASE.search(text)
        for text, _ in texts
    )
    assemblage_positive = any(
        _SINGLE_LIQUID_ASSEMBLAGE.fullmatch(" ".join(text.strip().split()))
        for text in assemblages
    )
    assemblage_unresolved = any(
        re.search(r"\b(?:l|liq\.?|liquid|glass)\b", text, re.IGNORECASE)
        and not _SINGLE_LIQUID_ASSEMBLAGE.fullmatch(" ".join(text.strip().split()))
        for text in assemblages
    )
    phase_analysis_rows = tuple(
        row
        for row in rows
        if str(
            _single_liquid_row_values(row).get("quantity") or row.get("type") or ""
        )
        == Quantity.EQUILIBRIUM_PHASE_COMPOSITION.value
    )
    identified_phases = tuple(
        phase
        for row in phase_analysis_rows
        if (phase := _single_liquid_phase_record(row)) is not None
    )
    phase_two_liquid = any(
        re.search(r"\b(?:l|liq|liquid)\s*[12]\b", phase)
        for phase in identified_phases
    )
    phase_crystal = any(
        phase in {"s", "solid", "c", "crystal", "crystalline"}
        or _SOLID_ASSEMBLAGE.search(phase)
        or _is_crystal_bearing(phase, include_single_liquid_phases=True)
        for phase in identified_phases
    )
    phase_records_positive = (
        bool(phase_analysis_rows)
        and len(identified_phases) == len(phase_analysis_rows)
        and all(phase in _SINGLE_LIQUID_PHASES for phase in identified_phases)
    )
    if phase_two_liquid:
        return "two_liquid_assemblage"
    if phase_crystal:
        return "crystal_bearing_assemblage"
    if two_liquid:
        return "two_liquid_assemblage"
    if crystals:
        return (
            "contradictory_single_liquid_evidence"
            if phrase_positive
            else "crystal_bearing_assemblage"
        )
    if negated:
        return "contradictory_single_liquid_evidence"
    if assemblage_unresolved:
        return "phase_assemblage_not_single_liquid"
    if phrase_positive or assemblage_positive or phase_records_positive:
        return None
    return "single_liquid_not_stated"


def _single_liquid_caveats(
    rows: Sequence[Mapping[str, object]],
    experiment: Mapping[str, object] | None,
    locator_for: Callable[[object], Locator | None],
) -> tuple[SingleLiquidGlassCaveat, SingleLiquidGlassCaveat]:
    evidence_rows = (() if experiment is None else (experiment,)) + tuple(rows)
    texts = tuple(
        item
        for row in evidence_rows
        for item in _single_liquid_field_texts(row, locator_for)
    )

    def caveat(
        pattern: re.Pattern[str],
        *,
        components: bool,
        component_loss: bool = False,
    ) -> SingleLiquidGlassCaveat:
        matched = tuple(
            (text, locator)
            for text, locator in texts
            if pattern.search(text)
            or (
                component_loss
                and _VOLATILE_COMPONENT_LOSS.search(text)
                and not _CONTAINER_EXCHANGE_EVIDENCE.search(text)
            )
        )
        if not matched:
            return SingleLiquidGlassCaveat("not_stated")
        snippets = tuple(dict.fromkeys(" ".join(text.split()) for text, _ in matched))
        if components:
            mentioned = tuple(
                label
                for marker, label in _SINGLE_LIQUID_COMPONENT_MARKERS
                if any(marker.search(text) for text, _ in matched)
            )
            if mentioned:
                snippets = (
                    *snippets,
                    "volatile components mentioned: " + ", ".join(mentioned),
                )
        return SingleLiquidGlassCaveat("stated", snippets, matched[0][1])

    return (
        caveat(_VOLATILE_LOSS_EVIDENCE, components=True, component_loss=True),
        caveat(_CONTAINER_EXCHANGE_EVIDENCE, components=False),
    )


def decide_single_liquid_glass_composition(
    *,
    experiments: Sequence[Mapping[str, object]],
    observations: Sequence[tuple[str, str, Mapping[str, object]]],
    oxide_map_for_row: Callable[[Mapping[str, object], str], Mapping[str, Decimal] | None],
    composition_for_oxides: Callable[[Mapping[str, Decimal], Locator | None], Composition | None],
    locator_for: Callable[[object], Locator | None],
) -> dict[str, SingleLiquidGlassDecision]:
    """Decide and project a same-run glass input from all located extract rows.

    Run aliases use only identical printed experiment/sample identifiers. The
    source must prove one liquid without crystals, and a unique liquid/glass
    phase-composition record must project through the existing oxide mapper.
    """

    experiments_by_id: dict[str, list[Mapping[str, object]]] = {}
    for experiment in experiments:
        raw_id = experiment.get("experiment_id") or experiment.get("id")
        if raw_id not in (None, ""):
            experiments_by_id.setdefault(str(raw_id), []).append(experiment)

    sample_to_experiment: dict[tuple[str, str], set[str]] = {}
    for experiment_id, entries in experiments_by_id.items():
        for experiment in entries:
            sample = experiment.get("sample")
            if not isinstance(sample, Mapping):
                continue
            for key, canonical_key in (
                *_SINGLE_LIQUID_SAMPLE_ID_ALIASES,
                ("id", "sample_id"),
            ):
                raw = _single_liquid_text(sample.get(key))
                if raw is not None:
                    sample_to_experiment.setdefault(
                        (canonical_key, raw), set()
                    ).add(experiment_id)

    groups: dict[str, list[tuple[str, str, Mapping[str, object]]]] = {}
    ambiguous_rows: set[str] = set()
    for observation_id, formula, row in observations:
        identifier = _single_liquid_identifier(row)
        if identifier is None:
            ambiguous_rows.add(observation_id)
            continue
        kind, raw_id = identifier
        if kind == "experiment":
            if raw_id in experiments_by_id:
                run_id = raw_id
            else:
                sample_matches = {
                    experiment_id
                    for (_sample_kind, sample_id), experiment_ids in sample_to_experiment.items()
                    if sample_id == raw_id
                    for experiment_id in experiment_ids
                }
                if len(sample_matches) > 1:
                    ambiguous_rows.add(observation_id)
                    continue
                run_id = next(iter(sample_matches), raw_id)
        elif (matches := sample_to_experiment.get((kind, raw_id))) is not None:
            if len(matches) != 1:
                ambiguous_rows.add(observation_id)
                continue
            run_id = next(iter(matches))
        else:
            run_id = f"{kind}:{raw_id}"
        groups.setdefault(run_id, []).append((observation_id, formula, row))

    decisions: dict[str, SingleLiquidGlassDecision] = {}
    for run_id, members in groups.items():
        run_rows = tuple(row for _observation_id, _formula, row in members)
        matching_experiments = experiments_by_id.get(run_id, [])
        experiment = matching_experiments[0] if len(matching_experiments) == 1 else None
        ambiguous_experiment = len(matching_experiments) > 1
        reason = _single_liquid_reason(run_rows, experiment, locator_for)
        row_sample_ids: dict[str, set[str]] = {}
        for row in run_rows:
            for kind, identifiers in _single_liquid_sample_ids(row).items():
                row_sample_ids.setdefault(kind, set()).update(identifiers)
        experiment_sample_ids = (
            _single_liquid_sample_ids(experiment)
            if experiment is not None
            else {}
        )
        if any(len(ids) > 1 for ids in row_sample_ids.values()) or any(
            len(ids) > 1 for ids in experiment_sample_ids.values()
        ) or any(
            row_sample_ids[kind] != experiment_sample_ids[kind]
            for kind in row_sample_ids.keys() & experiment_sample_ids.keys()
        ):
            reason = "ambiguous_run_id"
        if ambiguous_experiment:
            reason = "ambiguous_run_id"
        elif reason is None and _single_liquid_experiment_has_composition(experiment):
            reason = "run_composition_already_stated"
        volatile_loss, container_exchange = _single_liquid_caveats(
            run_rows, experiment, locator_for
        )
        if reason is not None:
            for observation_id, _formula, row in members:
                values = _single_liquid_row_values(row)
                quantity = str(values.get("quantity") or row.get("type") or "")
                decisions[observation_id] = SingleLiquidGlassDecision(
                    run_id=run_id,
                    status=(
                        "skipped"
                        if quantity == Quantity.EQUILIBRIUM_PHASE_COMPOSITION.value
                        else "not_target"
                    ),
                    reason=reason,
                    volatile_loss=volatile_loss,
                    container_exchange=container_exchange,
                )
            continue
        candidates: list[
            tuple[tuple[str, ...], Mapping[str, object], Mapping[str, Decimal], Locator]
        ] = []
        candidate_locator_missing = False
        scalar_components: dict[str, Decimal] = {}
        scalar_observation_ids: list[str] = []
        scalar_locator: Locator | None = None
        scalar_conflict = False
        for observation_id, formula, row in members:
            values = _single_liquid_row_values(row)
            quantity = str(values.get("quantity") or row.get("type") or "")
            if quantity != Quantity.EQUILIBRIUM_PHASE_COMPOSITION.value:
                continue
            phase = _single_liquid_phase_record(row)
            if phase is not None and phase not in _SINGLE_LIQUID_PHASES:
                continue
            if phase is None and reason is not None:
                continue
            status = str(values.get("admission_status") or row.get("admission_status") or "admitted")
            if status not in {"", "admitted"}:
                continue
            weights = oxide_map_for_row(row, formula)
            if weights:
                candidate_locator = locator_for(row)
                if candidate_locator is None:
                    candidate_locator_missing = True
                else:
                    if len(weights) >= 2:
                        candidates.append(((observation_id,), row, weights, candidate_locator))
                    else:
                        component, amount = next(iter(weights.items()))
                        previous = scalar_components.get(component)
                        if previous is not None and previous != amount:
                            scalar_conflict = True
                        else:
                            scalar_components[component] = amount
                            scalar_observation_ids.append(observation_id)
                            scalar_locator = scalar_locator or candidate_locator
        if scalar_conflict:
            reason = "ambiguous_glass_analysis"
        elif len(scalar_components) >= 2 and scalar_locator is not None:
            candidates.append(
                (
                    tuple(scalar_observation_ids),
                    {},
                    scalar_components,
                    scalar_locator,
                )
            )
        if reason is None and not candidates:
            non_oxide_candidate = any(
                str(_single_liquid_row_values(row).get("quantity") or row.get("type") or "")
                == Quantity.EQUILIBRIUM_PHASE_COMPOSITION.value
                and _single_liquid_phase_record(row) in _SINGLE_LIQUID_PHASES
                for row in run_rows
            )
            reason = (
                "glass_analysis_non_oxide_not_projected"
                if non_oxide_candidate
                else "glass_analysis_locator_missing"
                if candidate_locator_missing
                else "glass_analysis_not_found"
            )

        unique_candidates: dict[
            tuple[tuple[str, Decimal], ...],
            tuple[Mapping[str, object], Locator, list[str]],
        ] = {}
        if reason is None:
            for observation_ids, row, weights, locator in candidates:
                identity = tuple(sorted((str(name), Decimal(amount)) for name, amount in weights.items()))
                previous = unique_candidates.get(identity)
                if previous is None:
                    unique_candidates[identity] = (row, locator, list(observation_ids))
                else:
                    previous[2].extend(observation_ids)
            if len(unique_candidates) > 1:
                reason = "ambiguous_glass_analysis"

        base_composition: Composition | None = None
        redox_composition: Composition | None = None
        redox_weights: dict[str, Decimal] | None = None
        glass_ids: tuple[str, ...] = ()
        composition_ids: tuple[str, ...] = ()
        glass_locator: Locator | None = None
        if reason is None:
            weights = dict(next(iter(unique_candidates)))
            row, glass_locator, ids = next(iter(unique_candidates.values()))
            composition_ids = tuple(ids)
            glass_ids = tuple(
                observation_id
                for observation_id, _formula, candidate_row in members
                if str(
                    _single_liquid_row_values(candidate_row).get("quantity")
                    or candidate_row.get("type")
                    or ""
                )
                == Quantity.EQUILIBRIUM_PHASE_COMPOSITION.value
                and (
                    (phase := _single_liquid_phase_record(candidate_row)) is None
                    or phase in _SINGLE_LIQUID_PHASES
                )
            )
            base_composition = composition_for_oxides(dict(weights), glass_locator)
            if base_composition is None:
                reason = "glass_analysis_not_complete_oxide_vector"
            else:
                # d-305 derives FeOt in feo_t_weight_percent using
                # 2*M_FeO/M_Fe2O3. Convert only total iron into the formula-unit
                # key consumed by engines; the measured split never enters redox.
                redox_weights, _excluded = analysed_melt_weight_map(
                    weights, formula="", redox=True
                )
                redox_weights = {
                    name: Decimal(str(amount)) for name, amount in redox_weights.items()
                }
                if "FeO" in redox_weights and (
                    "FeO" in weights or "Fe2O3" in weights
                ):
                    # Preserve the total-iron derivation token through the
                    # existing FeOT-to-FeO formula-unit projection.
                    redox_weights["FeOT"] = redox_weights.pop("FeO")
                redox_composition = composition_for_oxides(
                    redox_weights,
                    glass_locator,
                )
                if redox_composition is None:
                    reason = "glass_analysis_redox_total_iron_not_projected"

        if reason is None:
            assert base_composition is not None
            base_composition = replace(
                base_composition,
                proxy_flag=COMPOSITION_FROM_SINGLE_LIQUID_GLASS,
                proxy_source=composition_ids[0],
                analysis_selection_rule="same printed run/sample id; single-liquid glass analysis",
                input_provenance=COMPOSITION_FROM_SINGLE_LIQUID_GLASS,
            )
            assert redox_composition is not None
            redox_composition = replace(
                redox_composition,
                proxy_flag=COMPOSITION_FROM_SINGLE_LIQUID_GLASS,
                proxy_source=composition_ids[0],
                analysis_selection_rule=(
                    "same printed run/sample id; single-liquid glass analysis; "
                    f"{SINGLE_LIQUID_GLASS_REDOX_FEOT_ONLY}"
                ),
                input_provenance=COMPOSITION_FROM_SINGLE_LIQUID_GLASS,
            )

        for observation_id, _formula, row in members:
            values = _single_liquid_row_values(row)
            quantity = str(values.get("quantity") or row.get("type") or "")
            is_glass_record = observation_id in glass_ids
            if reason is not None:
                decisions[observation_id] = SingleLiquidGlassDecision(
                    run_id=run_id,
                    status=(
                        "skipped"
                        if quantity == Quantity.EQUILIBRIUM_PHASE_COMPOSITION.value
                        else "not_target"
                    ),
                    reason=reason,
                    volatile_loss=volatile_loss,
                    container_exchange=container_exchange,
                )
                continue
            if quantity == Quantity.EQUILIBRIUM_PHASE_COMPOSITION.value:
                # Phase-composition quantities are the analysis itself, so
                # retain them as typed condition evidence but never score them.
                if is_glass_record:
                    decisions[observation_id] = SingleLiquidGlassDecision(
                        run_id=run_id,
                        status="applied",
                        reason=(
                            RefusalReason.INPUT_COMPOSITION_IS_SCORED_PHASE_ANALYSIS.value
                        ),
                        composition=base_composition,
                        composition_wt_percent=tuple(
                            sorted(
                                (str(name), Decimal(amount))
                                for name, amount in weights.items()
                            )
                        ),
                        composition_locator=glass_locator,
                        glass_observation_ids=glass_ids,
                        consumed_as_condition=True,
                        volatile_loss=volatile_loss,
                        container_exchange=container_exchange,
                    )
                continue
            if _single_liquid_row_has_composition(row):
                decisions[observation_id] = SingleLiquidGlassDecision(
                    run_id=run_id,
                    status="not_target",
                    reason="observation already carries a composition",
                    glass_observation_ids=glass_ids,
                    volatile_loss=volatile_loss,
                    container_exchange=container_exchange,
                )
                continue
            redox = quantity in {
                Quantity.FE3_FE2_RATIO.value,
                Quantity.VALENCE_RATIO.value,
            }
            decisions[observation_id] = SingleLiquidGlassDecision(
                run_id=run_id,
                status="applied",
                reason=COMPOSITION_FROM_SINGLE_LIQUID_GLASS,
                composition=redox_composition if redox else base_composition,
                composition_wt_percent=tuple(
                    sorted(
                        (str(name), Decimal(amount))
                for name, amount in (
                            redox_weights.items()
                            if redox and redox_weights is not None
                    else weights.items()
                        )
                    )
                ),
                composition_locator=glass_locator,
                glass_observation_ids=glass_ids,
                volatile_loss=volatile_loss,
                container_exchange=container_exchange,
            )

    for observation_id in ambiguous_rows:
        decisions[observation_id] = SingleLiquidGlassDecision(
            run_id=f"ambiguous:{observation_id}",
            status="skipped",
            reason="ambiguous_run_id",
        )
    return decisions


def _single_liquid_row_has_composition(row: Mapping[str, object]) -> bool:
    values = _single_liquid_row_values(row)
    point_conditions = row.get("point_conditions")
    if isinstance(point_conditions, Mapping):
        composition = point_conditions.get("composition")
        if isinstance(composition, Mapping):
            state = composition.get("state")
            if isinstance(state, Mapping) and str(state.get("tag") or "") == "value":
                return True
            if isinstance(state, Mapping) and str(state.get("tag") or "") == "unknown":
                reason = str(state.get("reason") or "").casefold()
                if not any(
                    token in reason
                    for token in (
                        "missing",
                        "not reported",
                        "not_reported",
                        "not stated",
                        "not_printed",
                    )
                ):
                    return True
    return any(
        values.get(key) not in (None, "")
        for key in (
            "composition_mass_percent",
            "composition_wt_pct",
            "oxide_wt_percent",
            "oxide_wt_pct",
            "composition_wt_percent",
            "glass_composition_wt_pct",
            "glass_composition_wt_percent",
            "composition_mol",
            "composition_mole_percent",
            "composition_mole_fraction",
            "components",
            "composition",
        )
    )


def _single_liquid_experiment_has_composition(
    experiment: Mapping[str, object] | None,
) -> bool:
    """Keep a printed run composition ahead of the measured glass fallback."""

    if experiment is None:
        return False
    containers: list[Mapping[str, object]] = [experiment]
    for parent in (experiment, _single_liquid_row_values(experiment)):
        for key in ("sample", "conditions", "point_conditions"):
            nested = parent.get(key)
            if isinstance(nested, Mapping):
                containers.append(nested)
    fields = (
        "printed_composition",
        "composition",
        "composition_wt_pct",
        "composition_wt_percent",
        "composition_mass_percent",
        "oxide_wt_pct",
        "oxide_wt_percent",
        "starting_composition",
        "starting_bulk_composition",
        "initial_composition",
    )
    for container in containers:
        for field_name in fields:
            payload = container.get(field_name)
            if not isinstance(payload, Mapping):
                continue
            state = payload.get("state")
            if isinstance(state, Mapping):
                if str(state.get("tag") or "value") != "value":
                    continue
                payload = state.get("value")
            if not isinstance(payload, Mapping):
                continue
            components = payload.get("components")
            if isinstance(components, Mapping) and components:
                return True
            if isinstance(components, (list, tuple)) and components:
                return True
            if any(
                payload.get(key) not in (None, "")
                for key in (
                    "composition_mass_percent",
                    "composition_wt_pct",
                    "oxide_wt_percent",
                    "oxide_wt_pct",
                    "composition_wt_percent",
                )
            ):
                return True
    return False


def composition_with_provenance(composition: Composition, provenance: str) -> Composition:
    if provenance not in INPUT_PROVENANCE_TOKENS:
        raise ValueError(f"unknown input provenance {provenance!r}")
    if composition.input_provenance == provenance:
        return composition
    return replace(composition, input_provenance=provenance)


def printed_quench_glass_phase(text: str | None) -> bool:
    """Printed phase is quenched glass, including glass plus quench crystals.

    Bare glass is not this evidence. The quench-crystal regex names crystals
    and does not by itself make a phase an analysed glass. A clause that
    says whether the sample is glass is not stated does not count.
    """

    if not isinstance(text, str) or not text.strip():
        return False
    if _QUENCH_WORD.search(text) is None:
        return False
    affirmed = _GLASS_NOT_STATED.sub(" ", text)
    return _GLASS_WORD.search(affirmed) is not None


def phase_map_provenance(
    map_locator: Locator | None,
    row_locator: Locator | None,
    phase_text: str | None = None,
    analysed_phase_text: str | None = None,
) -> str | None:
    """Classify a phase-row oxide map only from printed evidence.

    Gorojovsky Table 1 is a starting-material locator on the composition map.
    A final-slag or analysed-phase note is the analysis even when the tables
    differ. A printed phase of quenched glass is that run's analysed glass.
    ``analysed_phase_text`` is the phase_as_analyzed field. It is quench-glass
    evidence only: the analysis-word scan does not read it. Bare glass, and a
    locator that does not say, leave provenance unset. Component values and
    table notes are not read.
    """

    note = ""
    if map_locator is not None and map_locator.note:
        note = map_locator.note.casefold()
    if any(token in note for token in _STARTING_PHRASE) or _STARTING_WORD.search(note):
        return PRINTED_STARTING_BULK
    if any(token in note for token in _ANALYSIS_NOTE) or _ANALYSIS_WORD.search(note):
        return SCORED_PHASE_ANALYSIS
    if (
        map_locator is not None
        and row_locator is not None
        and map_locator.table not in (None, "")
        and row_locator.table not in (None, "")
        and str(map_locator.table) != str(row_locator.table)
    ):
        return PRINTED_STARTING_BULK
    folded = phase_text.casefold() if isinstance(phase_text, str) else ""
    if any(token in folded for token in _ANALYSIS_NOTE) or _ANALYSIS_WORD.search(folded):
        return SCORED_PHASE_ANALYSIS
    if printed_quench_glass_phase(phase_text) or printed_quench_glass_phase(
        analysed_phase_text
    ):
        return SCORED_PHASE_ANALYSIS
    return None


def label_marks_initial(text: str) -> bool:
    return bool(_WORD_INITIAL.search(text)) and not _WORD_FINAL.search(text)


def label_marks_final(text: str) -> bool:
    return bool(_WORD_FINAL.search(text)) and not _WORD_INITIAL.search(text)


def feo_t_weight_percent(feo: Decimal, fe2o3: Decimal) -> Decimal:
    """FeO_T = FeO + (2*M_FeO/M_Fe2O3)*Fe2O3 on the printed wt% scale.

    2*M_FeO/M_Fe2O3 = 143.69/159.69 with the ruling's rounded masses.
    A missing oxide contributes zero. The split is not returned.
    """

    return feo + (_FEO_MASS_TIMES_TWO / _FE2O3_MASS) * fe2o3


def is_d323_candidate(
    quantity: Quantity | None,
    formula: str,
    phase_role: PhaseRole | None,
) -> bool:
    if quantity in _REDOX_QUANTITIES or quantity in _SATURATION_QUANTITIES:
        return True
    if quantity is not Quantity.EQUILIBRIUM_PHASE_COMPOSITION:
        return False
    if formula not in _NON_INPUT_MELT_FORMULAS:
        return False
    return phase_role is None or phase_role in _MELT_ROLES


def _phase_role_of(identity: Identity) -> PhaseRole | None:
    state = identity.phase_role
    if state is not None and state.is_value and isinstance(state.value, PhaseRole):
        return state.value
    return None


def _component_names(composition: Composition) -> set[str]:
    return {name for name, _amount in composition.components}


def input_composition_echo_detail(identity: Identity) -> dict[str, object] | None:
    """Refuse when the bulk provenance is the phase analysis being scored.

    Printed starting bulks, named-material mixtures of printed starts, and
    the d-323 majors-minus-scored construction are not this refusal.
    Amount equality is not consulted. A same-run initial melt uses this
    reason with detail input_is_same_run_analysed_melt.
    """

    state = identity.composition
    if (
        state is None
        or not state.is_value
        or not isinstance(state.value, Composition)
    ):
        return None
    composition = state.value
    provenance = composition.input_provenance
    formula = identity.species.formula if identity.species is not None else ""
    quantity = quantity_token(identity)
    names = _component_names(composition)

    def refused(detail: str) -> dict[str, object]:
        return {
            "reason": RefusalReason.INPUT_COMPOSITION_IS_SCORED_PHASE_ANALYSIS.value,
            "detail": detail,
            "input_provenance": provenance,
            "formula": formula,
        }

    if provenance == SAME_RUN_INITIAL_ANALYSED_MELT:
        return refused("input_is_same_run_analysed_melt")
    if provenance == COMPOSITION_FROM_SINGLE_LIQUID_GLASS:
        if quantity is Quantity.EQUILIBRIUM_PHASE_COMPOSITION:
            return refused("input_composition_is_scored_phase_analysis")
        if quantity in _REDOX_QUANTITIES:
            split = "FeO" in names and "Fe2O3" in names
            normalized_feot = (
                "FeO" in names
                and SINGLE_LIQUID_GLASS_REDOX_FEOT_ONLY
                in (composition.analysis_selection_rule or "")
            )
            total_iron = (
                bool(names.intersection(_TOTAL_IRON_AS_FEO_KEYS))
                or normalized_feot
            ) and not split
            if total_iron:
                return None
            return refused("input_composition_is_scored_phase_analysis")
        return None
    if provenance == ANALYSED_MELT_MINUS_SCORED:
        if is_d323_candidate(quantity, formula, _phase_role_of(identity)):
            return None
        return refused("input_composition_is_scored_phase_analysis")
    if provenance != SCORED_PHASE_ANALYSIS:
        return None
    if quantity in _REDOX_QUANTITIES:
        split = "FeO" in names and "Fe2O3" in names
        total_iron = bool(names.intersection(_TOTAL_IRON_AS_FEO_KEYS)) and not split
        if total_iron:
            return None
        return refused("input_composition_is_scored_phase_analysis")
    if formula and formula not in names and formula in _NON_INPUT_MELT_FORMULAS:
        return None
    return refused("input_composition_is_scored_phase_analysis")


def _clip(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) > 180:
        return collapsed[:177] + "..."
    return collapsed


def _located_strings(value: object, label: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, str) and value.strip():
        found.append((label, value.strip()))
        return found
    state = getattr(value, "state", None)
    if state is not None and getattr(state, "is_value", False):
        raw = state.value
        if isinstance(raw, str) and raw.strip():
            found.append((label, raw.strip()))
    locator = getattr(value, "locator", None)
    note = getattr(locator, "note", None)
    if isinstance(note, str) and note.strip():
        found.append((f"{label}.note", note.strip()))
    return found


def collect_run_texts(
    experiment: Experiment | None,
    observation: Observation | None = None,
    *,
    locator: Locator | None = None,
    point_conditions: Mapping[str, object] | None = None,
    derivation_relation: str | None = None,
) -> tuple[tuple[str, str], ...]:
    """Located prose the evidence reader is allowed to see.

    sample_state_note and quench_statement are not copied onto the migrated
    observation, so they are not read here. A second copy of those fields
    would be a different writer. The phase field is ``provenance.printed_phase``
    and is read on its own: the generic provenance loop skips that key, so
    deleting this read drops the phase words.
    """

    texts: list[tuple[str, str]] = []
    if experiment is not None:
        sample = experiment.sample
        for name in ("form", "characterization", "pretreatment", "container"):
            texts.extend(_located_strings(getattr(sample, name, None), f"sample.{name}"))
        texts.extend(_located_strings(experiment.locator, "experiment.locator"))
        schedule = experiment.thermal_schedule
        if schedule is not None:
            texts.extend(
                _located_strings(
                    schedule.method, "experiment.thermal_schedule.method"
                )
            )
            texts.extend(
                _located_strings(
                    schedule.cooling_or_quench,
                    "experiment.thermal_schedule.cooling_or_quench",
                )
            )
    if observation is not None:
        texts.extend(_located_strings(observation.locator, "observation.locator"))
        if point_conditions is None:
            point_conditions = observation.point_conditions
        if derivation_relation is None and observation.derivation is not None:
            derivation_relation = observation.derivation.relation
        provenance = observation.provenance or {}
        printed_phase = provenance.get("printed_phase")
        if isinstance(printed_phase, str) and printed_phase.strip():
            texts.append(("observation.phase", printed_phase.strip()))
        for key, value in provenance.items():
            if key == "printed_phase":
                continue
            if isinstance(value, str) and value.strip():
                texts.append((f"provenance.{key}", value.strip()))
    elif locator is not None:
        texts.extend(_located_strings(locator, "observation.locator"))
    if point_conditions:
        for key, value in point_conditions.items():
            texts.extend(_located_strings(value, f"point_conditions.{key}"))
    if isinstance(derivation_relation, str) and derivation_relation.strip():
        texts.append(("derivation.relation", derivation_relation.strip()))
    return tuple(texts)


def _liquidus_above(
    point_conditions: Mapping[str, object] | None,
    temperature_K: Decimal | None,
) -> str | None:
    if temperature_K is None or point_conditions is None:
        return None
    for key, value in point_conditions.items():
        token = str(key)
        if "liquidus" not in token.casefold():
            continue
        state = getattr(value, "state", None)
        raw = state.value if state is not None and getattr(state, "is_value", False) else value
        try:
            number = Decimal(str(raw))
        except Exception:
            continue
        if not number.is_finite():
            continue
        folded = token.casefold()
        if folded.endswith("_k") or folded.endswith("_kelvin"):
            if temperature_K > number:
                return f"{token}={number}"
        elif folded.endswith("_c"):
            if temperature_K - Decimal("273.15") > number:
                return f"{token}={number}"
    return None


def printed_single_melt_evidence(
    texts: Sequence[tuple[str, str]],
    *,
    point_conditions: Mapping[str, object] | None = None,
    temperature_K: Decimal | None = None,
) -> MeltEvidence:
    """Crystal-bearing wins unless the only crystals are quench crystals.

    Glass-only, sampled-from-the-liquid, and quench-glass are the positive
    classes. 'during crystallization' is not crystal-bearing. Quench
    crystals, including 'crystals formed on quench', are not crystals at
    the run temperature. A phase label of glass is not evidence: the
    caller does not pass it. Pt-loop with a hyphen is sampled-from-the-liquid.
    """

    blobs = [(label, text) for label, text in texts if text.strip()]
    stripped = [(label, _QUENCH_CRYSTAL.sub(" ", text)) for label, text in blobs]
    for label, text in stripped:
        if _is_crystal_bearing(text):
            return MeltEvidence("crystalline", quote=f"{label}: {_clip(text)}")
    phase_hit: tuple[str, str] | None = None
    quench_sentence: tuple[str, str] | None = None
    crystal_hit: tuple[str, str] | None = None
    for label, text in blobs:
        if crystal_hit is None and (
            _ANALYZED_QUENCH_IS_LIQUID.search(text) or _QUENCH_CRYSTAL.search(text)
        ):
            crystal_hit = (label, text)
        if phase_hit is None and printed_quench_glass_phase(text):
            phase_hit = (label, text)
        if label.endswith("cooling_or_quench"):
            quench_sentence = (label, text)
        elif (
            quench_sentence is None
            and label.endswith("thermal_schedule.method")
            and _QUENCH_WORD.search(text)
        ):
            quench_sentence = (label, text)
    if phase_hit is not None:
        parts = [f"{phase_hit[0]}: {_clip(phase_hit[1])}"]
        if quench_sentence is not None and quench_sentence[0] != phase_hit[0]:
            parts.append(f"{quench_sentence[0]}: {_clip(quench_sentence[1])}")
        return MeltEvidence(
            "positive_quench",
            notice_reason="single_melt_evidence_quench_glass",
            quote="; ".join(parts),
        )
    if crystal_hit is not None:
        return MeltEvidence(
            "positive_quench",
            notice_reason="single_melt_evidence_quench_glass",
            quote=f"{crystal_hit[0]}: {_clip(crystal_hit[1])}",
        )
    for label, text in blobs:
        if _SAMPLED.search(text) and _FROM_THE_LIQUID.search(text):
            return MeltEvidence(
                "positive_sampled",
                notice_reason="single_melt_evidence_sampled_from_liquid",
                quote=f"{label}: {_clip(text)}",
            )
    for label, text in blobs:
        if _GLASS_ONLY.search(text):
            return MeltEvidence(
                "positive_glass",
                notice_reason="single_melt_evidence_crystal_free",
                quote=f"{label}: {_clip(text)}",
            )
    for label, text in blobs:
        if _ABOVE_LIQUIDUS.search(text):
            return MeltEvidence(
                "positive_liquidus",
                notice_reason="single_melt_evidence_above_liquidus",
                quote=f"{label}: {_clip(text)}",
            )
    liquidus = _liquidus_above(point_conditions, temperature_K)
    if liquidus is not None:
        return MeltEvidence(
            "positive_liquidus",
            notice_reason="single_melt_evidence_above_liquidus",
            quote=liquidus,
        )
    return MeltEvidence("unstated")


def saturation_defined(quantity: Quantity | None, texts: Sequence[tuple[str, str]]) -> bool:
    """A quantity whose saturating phase is its definition.

    Sulfide capacity with printed FeS or sulfide. Chloride capacity with a
    saturating salt. The saturating phase may be crystalline.
    """

    blob = " ".join(text for _label, text in texts).casefold()
    if quantity is Quantity.SULFIDE_CAPACITY:
        return re.search(r"\b(fes|sulfide|sulphide)\b", blob) is not None
    if quantity is Quantity.CHLORIDE_CAPACITY:
        return "saturat" in blob and re.search(r"\b(salt|chloride|nacl|kcl)\b", blob) is not None
    return False


def _refusal_detail(
    reason: RefusalReason,
    provenance: str | None,
    formula: str,
) -> dict[str, object]:
    return {
        "reason": reason.value,
        "detail": reason.value,
        "input_provenance": provenance,
        "formula": formula,
    }


def analysed_melt_decision(
    *,
    provenance: str | None,
    quantity: Quantity | None,
    formula: str,
    phase_role: PhaseRole | None,
    texts: Sequence[tuple[str, str]],
    point_conditions: Mapping[str, object] | None = None,
    temperature_K: Decimal | None = None,
) -> InputDecision:
    """d-323. Silence refuses. Positive evidence, or a saturation definition, rewrites.

    same_run_initial_analysed_melt is not this construction: the initial
    analysis is a different measurement from the final major-oxide row.
    An unclassified composition (provenance None) is left on the old gates.
    """

    if provenance == ANALYSED_MELT_MINUS_SCORED:
        if is_d323_candidate(quantity, formula, phase_role):
            return InputDecision("rewrite")
        return InputDecision("ignore")
    if provenance != SCORED_PHASE_ANALYSIS:
        return InputDecision("ignore")
    if not is_d323_candidate(quantity, formula, phase_role):
        return InputDecision("ignore")
    if saturation_defined(quantity, texts):
        return InputDecision(
            "rewrite",
            evidence_reason="single_melt_evidence_saturation_definition",
        )
    evidence = printed_single_melt_evidence(
        texts,
        point_conditions=point_conditions,
        temperature_K=temperature_K,
    )
    if evidence.positive:
        return InputDecision(
            "rewrite",
            evidence_reason=evidence.notice_reason,
            quote=evidence.quote,
        )
    if evidence.crystalline:
        return InputDecision(
            "refuse",
            refusal=RefusalReason.INPUT_PHASE_ASSEMBLAGE_CRYSTALLINE,
            detail=_refusal_detail(
                RefusalReason.INPUT_PHASE_ASSEMBLAGE_CRYSTALLINE,
                provenance,
                formula,
            ),
            quote=evidence.quote,
        )
    return InputDecision(
        "refuse",
        refusal=RefusalReason.INPUT_PHASE_ASSEMBLAGE_UNSTATED,
        detail=_refusal_detail(
            RefusalReason.INPUT_PHASE_ASSEMBLAGE_UNSTATED,
            provenance,
            formula,
        ),
    )


def analysed_melt_weight_map(
    weights: Mapping[str, float],
    *,
    formula: str,
    redox: bool,
) -> tuple[dict[str, float], tuple[str, ...]]:
    """Engine wt% for a d-323 rewrite. Remainder is not renormalized.

    Redox collapses the split to the FeO_T total and passes that number
    under FeO. FeO_T is not a species formula: composition_kg_and_mol
    rejects the underscore. A lone total-iron key is renamed to FeO and
    is not scaled again. A non-redox row drops the scored formula.
    """

    out = {str(name): float(amount) for name, amount in weights.items()}
    excluded: list[str] = []
    if redox:
        feo = Decimal(str(out.pop("FeO"))) if "FeO" in out else None
        fe2o3 = Decimal(str(out.pop("Fe2O3"))) if "Fe2O3" in out else None
        if feo is not None:
            excluded.append("FeO")
        if fe2o3 is not None:
            excluded.append("Fe2O3")
        carried: Decimal | None = None
        for key in _TOTAL_IRON_AS_FEO_KEYS:
            if key not in out:
                continue
            carried = Decimal(str(out.pop(key)))
            excluded.append(key)
            break
        for key in list(out):
            if key in _TOTAL_IRON_AS_FEO_KEYS:
                excluded.append(key)
                del out[key]
        if feo is not None or fe2o3 is not None:
            out["FeO"] = float(
                feo_t_weight_percent(
                    feo if feo is not None else Decimal("0"),
                    fe2o3 if fe2o3 is not None else Decimal("0"),
                )
            )
        elif carried is not None:
            out["FeO"] = float(carried)
        return out, tuple(excluded)
    if formula and formula in out:
        del out[formula]
        excluded.append(formula)
    return out, tuple(excluded)


def input_rewrite_notices(
    *,
    quantity: Quantity,
    origin: str,
    evidence_reason: str | None,
    quote: str | None,
    excluded: Sequence[str],
    iron_passed_as: str | None = None,
) -> tuple[Notice, ...]:
    notices: list[Notice] = []
    if evidence_reason:
        reason = evidence_reason if not quote else f"{evidence_reason}: {quote}"
        notices.append(
            Notice(
                kind=NoticeKind.ENGINE_MODEL_CAVEAT,
                affected_quantities=(quantity,),
                reason=reason,
                origin=origin,
            )
        )
    named = ",".join(excluded) if excluded else "none"
    reason = f"input_composition_from_analysed_melt: excluded={named}"
    if iron_passed_as:
        reason = f"{reason}; FeO_T_passed_as={iron_passed_as}"
    notices.append(
        Notice(
            kind=NoticeKind.INPUT_OMITTED,
            affected_quantities=(quantity,),
            reason=reason,
            origin=origin,
            dropped=tuple(excluded) if excluded else None,
        )
    )
    return tuple(notices)


def crystal_prediction_notice(quantity: Quantity, origin: str) -> Notice:
    return Notice(
        kind=NoticeKind.ENGINE_MODEL_CAVEAT,
        affected_quantities=(quantity,),
        reason="engine_predicts_crystals_from_analysed_liquid",
        origin=origin,
    )


def single_phase_printed_bulk_policy(
    *,
    provenance: str | None,
    quantity: Quantity | None,
    liquid_fraction: float | None,
    evidence: MeltEvidence,
) -> SinglePhaseBulkPolicy | None:
    """The one d-325 hook.

    100% liquid is liquid_fraction >= 1 - 1e-6, and only a reported fraction
    says that. A missing or non-numeric fraction from the caller is unknown.
    Unknown does not score a printed bulk whose assemblage is unstated or
    whose evidence is positive: that refusal is
    single_phase_bulk_identity_assemblage_unstated. A run that already prints
    crystals still scores, and the single-liquid notice is not attached,
    because both the crystal prediction and the crystalline-experiment case
    score. Case 1 (fraction below the threshold) returns None.
    """

    if provenance not in _PRINTED_BULK_TOKENS:
        return None
    if quantity is not Quantity.EQUILIBRIUM_PHASE_COMPOSITION:
        return None
    fraction: float | None
    if liquid_fraction is None:
        fraction = None
    else:
        try:
            fraction = float(liquid_fraction)
        except (TypeError, ValueError):
            fraction = None
    if fraction is None or fraction != fraction:
        if evidence.crystalline:
            return None
        return SinglePhaseBulkPolicy(
            refusal=RefusalReason.SINGLE_PHASE_BULK_IDENTITY_ASSEMBLAGE_UNSTATED
        )
    if fraction < SINGLE_LIQUID_MASS_FRACTION:
        return None
    if evidence.crystalline:
        return SinglePhaseBulkPolicy(
            notice_reason="engine_predicts_liquid_experiment_crystalline"
        )
    if evidence.positive:
        return SinglePhaseBulkPolicy(refusal=RefusalReason.SINGLE_PHASE_BULK_IDENTITY)
    return SinglePhaseBulkPolicy(
        refusal=RefusalReason.SINGLE_PHASE_BULK_IDENTITY_ASSEMBLAGE_UNSTATED
    )


def liquid_experiment_notice(
    quantity: Quantity,
    origin: str,
    *,
    quote: str | None,
) -> Notice:
    reason = "engine_predicts_liquid_experiment_crystalline"
    if quote:
        reason = f"{reason}: {quote}"
    return Notice(
        kind=NoticeKind.ENGINE_MODEL_CAVEAT,
        affected_quantities=(quantity,),
        reason=reason,
        origin=origin,
    )
