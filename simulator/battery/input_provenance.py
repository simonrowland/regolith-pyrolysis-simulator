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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from simulator.battery.enums import (
    NoticeKind,
    PhaseRole,
    Quantity,
    RefusalReason,
)
from simulator.battery.identity import Identity, quantity_token
from simulator.battery.records import Composition, Experiment, Locator, Notice, Observation

SCORED_PHASE_ANALYSIS = "scored_phase_analysis"
PRINTED_STARTING_BULK = "printed_starting_bulk"
NAMED_MATERIAL_MIXTURE = "named_material_mixture_of_printed_starts"
ANALYSED_MELT_MINUS_SCORED = "analysed_melt_minus_scored"
SAME_RUN_INITIAL_ANALYSED_MELT = "same_run_initial_analysed_melt"

INPUT_PROVENANCE_TOKENS = frozenset(
    {
        SCORED_PHASE_ANALYSIS,
        PRINTED_STARTING_BULK,
        NAMED_MATERIAL_MIXTURE,
        ANALYSED_MELT_MINUS_SCORED,
        SAME_RUN_INITIAL_ANALYSED_MELT,
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
_TOTAL_IRON_KEYS = frozenset({"FeOT", "FeO_T", "FeOt", "FeO*"})
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
)
# Word boundaries. "unbatched" and "nominally" are not a printed start.
_STARTING_WORD = re.compile(r"\b(?:batched|nominal)\b", re.IGNORECASE)
_ANALYSIS_NOTE = ("final slag", "product glass")
_ANALYSIS_WORD = re.compile(r"\banalys[ez]ed\b", re.IGNORECASE)

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


def phase_map_provenance(
    map_locator: Locator | None,
    row_locator: Locator | None,
) -> str:
    """A phase-row oxide map is the analysis, unless its own locator is a start.

    Gorojovsky Table 1 is a starting-material locator on the composition map.
    Bonnand's post-run glass map has no locator of its own. A final-slag or
    analysed-phase note stays the analysis even when the tables differ.
    Component values are not read.
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
    return SCORED_PHASE_ANALYSIS


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
    if provenance == ANALYSED_MELT_MINUS_SCORED:
        if is_d323_candidate(quantity, formula, _phase_role_of(identity)):
            return None
        return refused("input_composition_is_scored_phase_analysis")
    if provenance != SCORED_PHASE_ANALYSIS:
        return None
    if quantity in _REDOX_QUANTITIES:
        split = "FeO" in names and "Fe2O3" in names
        total_iron = bool(names & _TOTAL_IRON_KEYS) and not split
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
    would be a different writer.
    """

    texts: list[tuple[str, str]] = []
    if experiment is not None:
        sample = experiment.sample
        for name in ("form", "characterization", "pretreatment", "container"):
            texts.extend(_located_strings(getattr(sample, name, None), f"sample.{name}"))
        texts.extend(_located_strings(experiment.locator, "experiment.locator"))
    if observation is not None:
        texts.extend(_located_strings(observation.locator, "observation.locator"))
        if point_conditions is None:
            point_conditions = observation.point_conditions
        if derivation_relation is None and observation.derivation is not None:
            derivation_relation = observation.derivation.relation
        provenance = observation.provenance or {}
        for key, value in provenance.items():
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
    for label, text in blobs:
        if _ANALYZED_QUENCH_IS_LIQUID.search(text) or _QUENCH_CRYSTAL.search(text):
            return MeltEvidence(
                "positive_quench",
                notice_reason="single_melt_evidence_quench_glass",
                quote=f"{label}: {_clip(text)}",
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
        for key in ("FeO_T", "FeOT", "FeOt", "FeO*"):
            if key not in out:
                continue
            carried = Decimal(str(out.pop(key)))
            excluded.append(key)
            break
        for key in list(out):
            if key in _TOTAL_IRON_KEYS:
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
