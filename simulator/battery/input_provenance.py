"""Provenance of an engine-input composition, and the SC-458 / d-323 / d-325 rules.

migrate.py writes the token from locators, notes, and table identity. It does
not compare a component amount to the scored point. This module also owns the
printed-unknown row predicate used by migration and scoring. score.py refuses
from the token. This module imports neither score nor migrate.

Copies left elsewhere, on purpose:
- feedstock_composition.FEOT_FROM_FE2O3 is 2*71.844/159.687. The d-323 input
  construction uses the ruling's rounded masses, 2*M_FeO/M_Fe2O3 = 143.69/159.69.
- catalogue proxies stay provenance-None. The echo guard does not invent a token.
- Raw element_wt_percent rows stay separate and are not stamped here. An
  existing stoichiometric-oxide projection attached to an analyzed liquid row
  can be used only as the flagged liquid input for a different phase.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from simulator.battery.decimal_tokens import _decimal_rounding_half_width
from simulator.battery.enums import (
    AmountBasis,
    NoticeKind,
    Phase,
    PhaseRole,
    Quantity,
    RefusalReason,
    ValueKind,
    effective_phase_basis,
)
from simulator.battery.identity import Identity, quantity_token
from simulator.battery.records import (
    Composition,
    Experiment,
    Located,
    Locator,
    Notice,
    Observation,
    State,
    phase_token,
)
from simulator.feedstock_composition import _TOTAL_IRON_AS_FEO_KEYS

SCORED_PHASE_ANALYSIS = "scored_phase_analysis"
PRINTED_STARTING_BULK = "printed_starting_bulk"
NAMED_MATERIAL_MIXTURE = "named_material_mixture_of_printed_starts"
ANALYSED_MELT_MINUS_SCORED = "analysed_melt_minus_scored"
SAME_RUN_INITIAL_ANALYSED_MELT = "same_run_initial_analysed_melt"
SAME_RUN_ANALYSED_LIQUID_FOR_OTHER_PHASE = (
    "same_run_analysed_liquid_for_other_phase"
)
_PHASE_COMPOSITION_MISSING_ORIGINAL_REASON = (
    "identity_incomplete:missing_input:composition"
)

INPUT_PROVENANCE_TOKENS = frozenset(
    {
        SCORED_PHASE_ANALYSIS,
        PRINTED_STARTING_BULK,
        NAMED_MATERIAL_MIXTURE,
        ANALYSED_MELT_MINUS_SCORED,
        SAME_RUN_INITIAL_ANALYSED_MELT,
        SAME_RUN_ANALYSED_LIQUID_FOR_OTHER_PHASE,
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
_ABOVE_LIQUIDUS = re.compile(
    r"above (?:the |its )?(?:dry )?liquidus",
    re.IGNORECASE,
)
_WORD_INITIAL = re.compile(r"(?<![A-Za-z])initial(?![A-Za-z])", re.IGNORECASE)
_WORD_FINAL = re.compile(r"(?<![A-Za-z])final(?![A-Za-z])", re.IGNORECASE)


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


def _is_other_phase(identity: Identity) -> bool:
    role = _phase_role_of(identity)
    if role in {PhaseRole.METAL, PhaseRole.MATTE}:
        return True
    phase = phase_token(identity.species) if identity.species is not None else None
    return role is None and phase in {Phase.CR, Phase.G}


def _row_printed_composition_unknown(
    point_conditions: Mapping[str, Located[object]] | None,
) -> bool:
    """Whether this row prints its composition cell as unknown."""

    conditions = point_conditions or {}
    for key in ("composition", "initial_composition_as_published"):
        located = conditions.get(key)
        if (
            isinstance(located, Located)
            and isinstance(located.state, State)
            and located.state.is_unknown
        ):
            return True
    return False


def _component_names(composition: Composition) -> set[str]:
    return {name for name, _amount in composition.components}


@dataclass(frozen=True)
class _PhaseCompositionInput:
    composition: Composition | None = None
    notice: Notice | None = None
    refusal_reason: RefusalReason | None = None
    refusal_detail: dict[str, object] | None = None


def _phase_row_composition_input(
    observation: Observation,
    experiment: Experiment | None,
    siblings: Sequence[Observation],
) -> _PhaseCompositionInput:
    """Choose a printed run input, or identify a liquid self-echo.

    A typed starting bulk wins. Without one, a complete analysed liquid can
    supply another phase in the same experiment. Scalar wt% rows need their
    printed sum to match at the precision shown before they can form a vector.
    """

    identity = observation.identity
    if (
        not isinstance(identity, Identity)
        or quantity_token(identity) is not Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        or (identity.composition is not None and identity.composition.is_value)
    ):
        return _PhaseCompositionInput()

    rows = {row.observation_id: row for row in siblings}
    rows[observation.observation_id] = observation

    def temperature(row_identity: Identity) -> Decimal | None:
        state = row_identity.temperature_K
        value = state.value if state is not None and state.is_value else None
        return value if isinstance(value, Decimal) and value.is_finite() else None

    def phase(row_identity: Identity) -> Phase | None:
        return phase_token(row_identity.species) if row_identity.species is not None else None

    def liquid(row_identity: Identity) -> bool:
        role = _phase_role_of(row_identity)
        return role in _MELT_ROLES or (
            role is None and phase(row_identity) in {Phase.L, Phase.GLASS, Phase.SUPERCOOLED_L}
        )

    def composition_sources(row: Observation) -> tuple[tuple[Composition, str], ...]:
        found: list[tuple[Composition, str]] = []
        row_identity = row.identity
        if isinstance(row_identity, Identity):
            state = row_identity.composition
            if (
                state is not None
                and state.is_value
                and isinstance(state.value, Composition)
            ):
                found.append((state.value, f"{row.observation_id}:identity.composition"))
        point = (row.point_conditions or {}).get("composition")
        if (
            isinstance(point, Located)
            and point.state.is_value
            and isinstance(point.state.value, Composition)
            and (
                point.inference is None
                or point.inference.relation
                in {
                    "wt_pct_to_mole_fraction",
                    "element_wt_percent_stoichiometric_oxide_projection",
                }
            )
        ):
            found.append((point.state.value, f"{row.observation_id}:point_conditions.composition"))
        return tuple(found)

    def signature(composition: Composition) -> tuple[object, ...]:
        return (
            composition.basis,
            tuple(sorted(composition.components)),
            composition.amount_basis,
            composition.proxy_flag,
            composition.proxy_source,
            composition.analysis_selection_rule,
        )

    def refusal(reason: str, sources: Sequence[str]) -> _PhaseCompositionInput:
        return _PhaseCompositionInput(
            refusal_reason=RefusalReason.IDENTITY_INCOMPLETE,
            refusal_detail={"reason": reason, "sources": sorted(sources)},
        )

    start_candidates: dict[tuple[object, ...], tuple[Composition, str]] = {}
    if experiment is not None:
        initial = experiment.sample.initial_composition
        if (
            initial is not None
            and initial.state.is_value
            and isinstance(initial.state.value, Composition)
            and initial.state.value.proxy_flag
            != NoticeKind.COMPOSITION_FROM_SAMPLE_CATALOG.value
            and initial.state.value.input_provenance
            in {None, PRINTED_STARTING_BULK, NAMED_MATERIAL_MIXTURE}
        ):
            composition = initial.state.value
            if composition.input_provenance is None:
                composition = composition_with_provenance(
                    composition, PRINTED_STARTING_BULK
                )
            start_candidates[signature(composition)] = (
                composition,
                "experiment.sample.initial_composition",
            )
    for row in rows.values():
        if row.experiment_id != observation.experiment_id:
            continue
        for composition, source in composition_sources(row):
            if composition.input_provenance not in _PRINTED_BULK_TOKENS:
                continue
            start_candidates[signature(composition)] = (composition, source)
    if len(start_candidates) > 1:
        return refusal("run_start_composition_ambiguous", [source for _, source in start_candidates.values()])
    if start_candidates:
        composition, source = next(iter(start_candidates.values()))
        return _PhaseCompositionInput(
            composition=composition,
            notice=Notice(
                kind=NoticeKind.COMPOSITION_FROM_RUN_SIBLINGS,
                affected_quantities=(Quantity.EQUILIBRIUM_PHASE_COMPOSITION,),
                reason=(
                    f"{NoticeKind.COMPOSITION_FROM_RUN_SIBLINGS.value}:"
                    f"original_reason={_PHASE_COMPOSITION_MISSING_ORIGINAL_REASON};"
                    f"source={source}"
                ),
                origin=observation.observation_id,
            ),
        )

    target_temperature = temperature(identity)
    target_role = _phase_role_of(identity)
    target_phase = phase(identity)
    target_is_liquid = liquid(identity)
    target_is_other_phase = _is_other_phase(identity)
    liquid_rows = tuple(
        row
        for row in rows.values()
        if row.observation_id != observation.observation_id
        and row.experiment_id == observation.experiment_id
        and isinstance(row.identity, Identity)
        and quantity_token(row.identity) is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        and liquid(row.identity)
        and target_temperature is not None
        and temperature(row.identity) == target_temperature
    )

    # The liquid analysis is an explicitly flagged proxy, not a run bulk
    # mass-balance claim. It is only offered to predict a different phase in
    # this run; the liquid's own rows remain the SC-458 self-echo. A full
    # vector is the least lossy representation, while a d-323 residual vector
    # is not the whole liquid and is ineligible.
    full_analyses: dict[tuple[object, ...], tuple[Composition, str]] = {}
    for row in liquid_rows:
        for composition, source in composition_sources(row):
            if (
                composition.proxy_flag
                == NoticeKind.COMPOSITION_FROM_SAMPLE_CATALOG.value
                or composition.input_provenance
                not in {
                    None,
                    SCORED_PHASE_ANALYSIS,
                    SAME_RUN_ANALYSED_LIQUID_FOR_OTHER_PHASE,
                }
            ):
                continue
            analysed = composition_with_provenance(
                composition, SAME_RUN_ANALYSED_LIQUID_FOR_OTHER_PHASE
            )
            full_analyses[signature(analysed)] = (analysed, source)

    analysis_groups: dict[
        tuple[PhaseRole | None, Phase | None, str], list[Observation]
    ] = {}
    for row in rows.values():
        row_identity = row.identity
        if (
            row.experiment_id != observation.experiment_id
            or not isinstance(row_identity, Identity)
            or quantity_token(row_identity) is not Quantity.EQUILIBRIUM_PHASE_COMPOSITION
            or not liquid(row_identity)
            or target_temperature is None
            or temperature(row_identity) != target_temperature
            or effective_phase_basis(
                row_identity.subtype.value
                if row_identity.subtype is not None and row_identity.subtype.is_value
                else None
            )
            != "wt_percent"
        ):
            continue
        role = _phase_role_of(row_identity)
        row_phase = phase(row_identity)
        subtype = (
            str(row_identity.subtype.value)
            if row_identity.subtype is not None and row_identity.subtype.is_value
            else ""
        )
        analysis_groups.setdefault((role, row_phase, subtype), []).append(row)

    assembled: dict[tuple[object, ...], tuple[Composition, str]] = {}
    for (_role, _phase, _subtype), group in analysis_groups.items():
        weights: dict[str, Decimal] = {}
        printed_sums: set[Decimal] = set()
        complete = True
        for row in group:
            row_identity = row.identity
            assert isinstance(row_identity, Identity)
            formula = row_identity.species.formula if row_identity.species is not None else ""
            value = row.value
            amount = value.point if value.kind is ValueKind.POINT else None
            printed_sum_value = (row.point_conditions or {}).get("printed_sum")
            printed_sum_state = (
                printed_sum_value.state
                if isinstance(printed_sum_value, Located)
                else None
            )
            raw_printed_sum = (
                printed_sum_state.value
                if printed_sum_state is not None and printed_sum_state.is_value
                else None
            )
            try:
                printed_sum = Decimal(str(raw_printed_sum))
            except (ArithmeticError, TypeError, ValueError):
                complete = False
                break
            if (
                not formula
                or amount is None
                or not amount.is_finite()
                or amount < 0
                or not printed_sum.is_finite()
                or formula in weights
            ):
                complete = False
                break
            weights[formula] = amount
            printed_sums.add(printed_sum)
        if (
            not complete
            or len(printed_sums) != 1
            or not weights
            or sum(weights.values(), Decimal(0)) <= 0
        ):
            continue
        printed_sum = next(iter(printed_sums))
        measured_sum = sum(weights.values(), Decimal(0))
        # Printed cells and their printed total are rounded independently.
        rounding_bound = _decimal_rounding_half_width(printed_sum)
        rounding_bound += sum(
            (_decimal_rounding_half_width(amount) for amount in weights.values()),
            Decimal(0),
        )
        if abs(measured_sum - printed_sum) > rounding_bound:
            continue
        composition = Composition(
            basis="printed_phase_analysis",
            components=tuple(weights.items()),
            amount_basis=AmountBasis.MASS_PERCENT,
            input_provenance=SAME_RUN_ANALYSED_LIQUID_FOR_OTHER_PHASE,
        )
        source = ";".join(sorted(row.observation_id for row in group))
        assembled[signature(composition)] = (composition, source)

    if target_is_liquid:
        same_phase_sibling = any(
            isinstance(row.identity, Identity)
            and quantity_token(row.identity) is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
            and liquid(row.identity)
            and _phase_role_of(row.identity) == target_role
            and phase(row.identity) == target_phase
            and temperature(row.identity) == target_temperature
            and row.observation_id != observation.observation_id
            and bool(composition_sources(row))
            for row in rows.values()
        )
        own_analysis = any(
            composition.input_provenance not in _PRINTED_BULK_TOKENS
            for composition, _source in composition_sources(observation)
        ) or same_phase_sibling or any(
            observation.observation_id in source.split(";")
            for _composition, source in assembled.values()
        )
        if own_analysis:
            return _PhaseCompositionInput(
                refusal_reason=RefusalReason.INPUT_COMPOSITION_IS_SCORED_PHASE_ANALYSIS,
                refusal_detail={
                    "reason": RefusalReason.INPUT_COMPOSITION_IS_SCORED_PHASE_ANALYSIS.value,
                    "detail": "input_is_same_run_analysed_melt",
                    "observation_id": observation.observation_id,
                },
            )
        return _PhaseCompositionInput()

    if not target_is_other_phase:
        return _PhaseCompositionInput()
    candidates = full_analyses or assembled
    if len(candidates) > 1:
        return refusal(
            "analysed_phase_composition_ambiguous",
            [source for _, source in candidates.values()],
        )
    if not candidates:
        return _PhaseCompositionInput()
    composition, source = next(iter(candidates.values()))
    return _PhaseCompositionInput(
        composition=composition,
        notice=Notice(
            kind=NoticeKind.COMPOSITION_FROM_ANALYSED_PHASE,
            affected_quantities=(Quantity.EQUILIBRIUM_PHASE_COMPOSITION,),
            reason=(
                f"{NoticeKind.COMPOSITION_FROM_ANALYSED_PHASE.value}:"
                f"original_reason={_PHASE_COMPOSITION_MISSING_ORIGINAL_REASON};"
                f"source={source}"
            ),
            origin=observation.observation_id,
        ),
    )


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

    if provenance == SAME_RUN_ANALYSED_LIQUID_FOR_OTHER_PHASE:
        if (
            quantity is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
            and _is_other_phase(identity)
        ):
            return None
        return refused("analysed_liquid_input_is_not_a_different_phase")
    if provenance == SAME_RUN_INITIAL_ANALYSED_MELT:
        return refused("input_is_same_run_analysed_melt")
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
        match = _CRYSTAL_BEARING.search(text)
        if match is not None:
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
