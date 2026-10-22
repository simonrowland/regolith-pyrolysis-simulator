"""Source oxygen conditions for Knudsen comparisons (owner decision d-216)."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from typing import Iterable, Mapping

from simulator.battery.enums import AdmissionStatus, EvidenceClass, NoticeKind, Phase, Quantity, UncertaintyKind, ValueKind
from simulator.battery.identity import Identity, quantity_token
from simulator.battery.records import Bench, Derivation, Located, Locator, Notice, Observation, Species, State, Uncertainty, Value, _is_source_internally_inconsistent
from simulator.battery.waypoints import identity_composition_waypoint
from simulator.physical_constants import GAS_CONSTANT

SOURCE_OXYGEN_RELATION = "source_oxygen_condition;"


def oxygen_pressure_point(row: Observation) -> Located | None:
    """The two stored names for an observation's oxygen pressure, in Pa."""
    points = [
        (row.point_conditions or {}).get(key)
        for key in ("fO2_Pa", "oxygen_partial_pressure_Pa")
    ]
    points = [point for point in points if point is not None and point.state.is_value]
    if not points:
        return None
    values = {
        point.state.value.point if isinstance(point.state.value, Value) else point.state.value
        for point in points
    }
    return points[0] if len(values) == 1 else None


def pressure_has_only_unit_conversion(point: Located) -> bool:
    """A known exact pressure-unit conversion preserves printed provenance."""
    if point.inference is None:
        return True
    from simulator.battery.migrate import _CONVERSION_META

    inference = point.inference
    meta = _CONVERSION_META.get(inference.relation)
    if meta is None or meta[2] != "Pa" or inference.output_unit != "Pa":
        return False
    parameters = dict(inference.parameters)
    original, factor = parameters.get("original"), parameters.get("factor")
    if (
        original is None or factor is None
        or not original.state.is_value or not factor.state.is_value
        or original.inference is not None or factor.inference is not None
        or factor.state.value != meta[0]
    ):
        return False
    value = point.state.value
    value = value.point if isinstance(value, Value) else value
    return value == original.state.value * factor.state.value


def printed_source_pressure(row: Observation) -> Decimal | None:
    point = oxygen_pressure_point(row)
    if point is not None and pressure_has_only_unit_conversion(point):
        value = point.state.value
        value = value.point if isinstance(value, Value) else value
        if isinstance(value, Decimal) and value.is_finite() and value > 0:
            return value
    return None


@dataclass(frozen=True)
class SourceOxygenCondition:
    observation: Observation
    used_observations: frozenset[str]


class SourceOxygenFallbackReason(StrEnum):
    NO_PRINTED_CONDITION = "no_oxygen_condition_printed"
    UNTYPED_LINEAGE = "lineage_untyped"
    AMBIGUOUS = "several_candidates_survive"
    SAMPLE_DERIVED = "derived_from_scored_sample_species"
    SOURCE_INCONSISTENT = "source_inconsistent"


@dataclass(frozen=True)
class SourceOxygenFallback:
    reason: SourceOxygenFallbackReason


def source_oxygen_closure_notice(quantity: Quantity, row: Observation) -> Notice:
    # Direct predictor calls have no sibling-source search. They can report
    # untyped linkage, but cannot assert that a source printed no oxygen.
    reason = SourceOxygenFallbackReason(
        (row.provenance or {}).get(
            "source_oxygen_fallback", SourceOxygenFallbackReason.UNTYPED_LINEAGE
        )
    )
    return Notice(
        kind=NoticeKind.INPUT_OMITTED,
        affected_quantities=(quantity,),
        reason=(
            "model assumption: vapour-only zero-storage closure; "
            f"source_oxygen_fallback={reason.value}"
        ),
        origin="source oxygen selection",
    )


def _point_key(row: Observation) -> tuple | None:
    identity = row.identity
    if (
        not isinstance(identity, Identity) or identity.temperature_K is None
        or not identity.temperature_K.is_value
    ):
        return None
    composition = identity_composition_waypoint(row)
    if composition is None:
        return None
    return row.experiment_id, identity.temperature_K.value, tuple(sorted(composition.value.items()))


def _lineage(row: Observation, rows: Mapping[str, Observation], seen: frozenset[str] = frozenset()) -> tuple[set[str], set[str]] | None:
    if row.observation_id in seen:
        return None
    seen = seen | {row.observation_id}
    species: set[str] = set()
    used = {row.observation_id}
    reaction = row.identity.reaction
    if reaction is not None and reaction.is_value:
        species.update(term.species.formula for term in reaction.value.terms)
    inputs = set(row.derived_from or ())
    if any(token not in rows for token in inputs):
        return None
    if row.derivation is not None:
        inputs.update(row.derivation.inputs)
    for token in inputs:
        parent = rows.get(token)
        if parent is None:
            continue  # PDF/compilation references are not gas observations.
        if _point_key(parent) != _point_key(row):
            return None
        species.add(parent.identity.species.formula)
        ancestry = _lineage(parent, rows, seen)
        if ancestry is None:
            return None
        species.update(ancestry[0])
        used.update(ancestry[1])
    return species - {"O", "O2"}, used


def _janaf_atomic_oxygen(temperature_K: Decimal) -> tuple[Decimal, float, Locator]:
    from simulator.battery.generators.janaf import (
        _fusion_table_points,
        _interpolate_formation_gibbs,
    )

    points, digest = _fusion_table_points("O-001")
    gibbs = _interpolate_formation_gibbs(points, temperature_K)
    constant = math.exp(-float(gibbs) * 1000.0 / (GAS_CONSTANT * float(temperature_K)))
    janaf = Locator(
        source_path="data/literature/compilations/janaf/tables/O-001.yaml",
        record=digest,
    )
    return gibbs, constant, janaf


def _po2_pa_from_atomic_oxygen(pressure_pa: Decimal, constant: float) -> Decimal:
    # Premise: JANAF O-001 is 1/2 O2 = O, o=K_O(T)*sqrt(q),
    # with o=pO/(1 bar), q=pO2/(1 bar), so q=(o/K_O)^2.
    # Source atm -> Pa -> bar BEFORE squaring; 1 atm=1.01325 bar.
    # At 1933 K JANAF G=126.02894 kJ/mol, K_O=3.930428016e-4:
    # W pO=6.8e-10 atm gives q=3.073062243e-12 bar. Shornikov's
    # 2.65e-9 atm O gives 4.667080363e-11 bar, agreeing within 0.36%
    # with its printed O2=4.59e-11 atm=4.6508175e-11 bar.
    return Decimal(str((float(pressure_pa) / 100000.0 / constant) ** 2 * 100000.0))


def _atomic_oxygen_equilibrium_parameters(
    temperature_K: Decimal,
    temperature_locator: Locator | None,
    gibbs: Decimal,
    constant: float,
    janaf: Locator,
) -> tuple[tuple[str, Located], ...]:
    return (
        ("temperature_K", Located(State.of(temperature_K), locator=temperature_locator)),
        ("JANAF_O_001_delta_fG_kJ_mol", Located(State.of(gibbs), locator=janaf)),
        ("K_O", Located(State.of(Decimal(str(constant))), locator=janaf)),
    )



def resolve_source_oxygen(
    reference: Observation,
    observations: Iterable[Observation],
    bench: Bench | None,
) -> SourceOxygenCondition | SourceOxygenFallback:
    """Select a unique printed condition using typed axes and gas ancestry."""
    rows = tuple(observations)
    oxygen_rows = [
        row for row in rows
        if row.source_id == reference.source_id
        and row.experiment_id == reference.experiment_id
        and quantity_token(row.identity) is Quantity.P_PARTIAL
        and row.identity.species.formula in {"O", "O2"}
    ]
    key = _point_key(reference)
    inconsistent = [reference, *(
        row for row in oxygen_rows if key is not None and _point_key(row) == key
    )]
    if any(
        _is_source_internally_inconsistent(notice.kind, notice.reason)
        for row in inconsistent for notice in row.notices
    ):
        return SourceOxygenFallback(SourceOxygenFallbackReason.SOURCE_INCONSISTENT)
    if key is None:
        return SourceOxygenFallback(
            SourceOxygenFallbackReason.UNTYPED_LINEAGE if oxygen_rows
            else SourceOxygenFallbackReason.NO_PRINTED_CONDITION
        )
    point = {
        row.observation_id: row for row in rows
        if quantity_token(row.identity) is Quantity.P_PARTIAL
        and row.source_id == reference.source_id and _point_key(row) == key
    }
    materials = {
        item.state.value.value for item in (bench.cell_materials or ())
        if item.state.is_value
    } if bench is not None else set()
    metal = next(iter(materials)) if len(materials) == 1 else None
    cell_pair = {f"{metal}O2", f"{metal}O3"} if metal in {"W", "Mo"} else set()
    sample_species = {
        row.identity.species.formula for row in point.values()
    } - cell_pair - {"O", "O2"}
    measured = []
    cell_oxygen = []
    printed = 0
    sample_derived = 0
    for row in point.values():
        formula = row.identity.species.formula
        if (
            formula not in {"O", "O2"} or row.locator is None or row.engine is not None
            or row.admission.status is not AdmissionStatus.ADMITTED
            or row.value.kind is not ValueKind.POINT or row.value.point is None
            or not row.value.point.is_finite() or row.value.point <= 0
        ):
            continue
        printed += 1
        ancestry = _lineage(row, point)
        if ancestry is None:
            continue
        if ancestry[0] & sample_species:
            sample_derived += 1
            continue
        gases, used = ancestry
        if formula == "O" and cell_pair and gases == cell_pair:
            if row.derived_from or row.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED:
                cell_oxygen.append((row, used))
        elif (
            row.evidence.class_.is_value
            and row.evidence.class_.value in {EvidenceClass.MEASURED_DIRECT, EvidenceClass.MEASURED_REDUCED}
            and not gases
            and not row.derived_from
        ):
            measured.append((row, used))
    candidates = measured or cell_oxygen
    if len(candidates) != 1:
        # Report the gate that prevented selection. Mixed untyped/sample
        # failures are untyped, not "all sample-derived"; ambiguity means
        # multiple independently eligible candidates, not raw column count.
        if len(candidates) > 1:
            reason = SourceOxygenFallbackReason.AMBIGUOUS
        elif printed and sample_derived == printed:
            reason = SourceOxygenFallbackReason.SAMPLE_DERIVED
        elif printed or any(_point_key(row) in (None, key) for row in oxygen_rows):
            reason = SourceOxygenFallbackReason.UNTYPED_LINEAGE
        else:
            reason = SourceOxygenFallbackReason.NO_PRINTED_CONDITION
        return SourceOxygenFallback(reason)
    source, used = candidates[0]
    assumption = "" if measured else "; cell-metal activity as assumed by the source"
    # A printed O2 reduction downstream of the selected atomic-O observation
    # is the same condition, not a second independent oxygen estimate.
    if not measured:
        molecular = [
            row for row in point.values()
            if row.identity.species.formula == "O2"
            and row.derived_from == (source.observation_id,)
            and row.value.kind is ValueKind.POINT and row.value.point is not None
            and row.value.point.is_finite() and row.value.point > 0
            and row.locator is not None
            and row.admission.status is AdmissionStatus.ADMITTED
            and (ancestry := _lineage(row, point)) is not None
            and ancestry[0] == cell_pair
        ]
        if len(molecular) > 1:
            return SourceOxygenFallback(SourceOxygenFallbackReason.AMBIGUOUS)
        if molecular:
            used = used | {molecular[0].observation_id}
            source = molecular[0]
    params = [("source_pressure_Pa", Located(State.of(source.value.point), locator=source.locator))]
    if source.identity.species.formula == "O":
        temperature = reference.identity.temperature_K.value
        gibbs, constant, janaf = _janaf_atomic_oxygen(temperature)
        pressure = _po2_pa_from_atomic_oxygen(source.value.point, constant)
        params.extend(
            _atomic_oxygen_equilibrium_parameters(
                temperature, source.locator, gibbs, constant, janaf
            )
        )
        arithmetic = "pO2_Pa=100000*(pO_Pa/100000/K_O)^2; JANAF O-001"
    else:
        pressure = source.value.point
        arithmetic = "printed pO2 in Pa (recorded source-unit conversion)"
    if source.derivation is not None:
        params.extend((name, value) for name, value in source.derivation.parameters if name == "cell_metal_activity")
    derivation = Derivation(
        relation=SOURCE_OXYGEN_RELATION + arithmetic + assumption,
        inputs=tuple(sorted(used)), parameters=tuple(params), output_unit="Pa",
    )
    condition = replace(
        source,
        observation_id=f"derived:source-oxygen:{source.observation_id}",
        identity=replace(source.identity, species=Species("O2", Phase.G), reaction=State.unknown("oxygen conditioning reduction")),
        value=Value.point_of(pressure),
        uncertainty=(
            Uncertainty(kind=UncertaintyKind.NONE)
            if source.identity.species.formula == "O" else source.uncertainty
        ),
        evidence=replace(
            source.evidence, class_=State.of(EvidenceClass.MODEL_DERIVED),
            model="source oxygen conditioning reduction (d-216)",
        ),
        derived_from=tuple(sorted(used)), derivation=derivation,
    )
    return SourceOxygenCondition(condition, frozenset(used))


def linked_source_pressure(row: Observation) -> Decimal | None:
    located = oxygen_pressure_point(row)
    if (
        located is not None and located.state.is_value
        and located.inference is not None
        and located.inference.relation.startswith(SOURCE_OXYGEN_RELATION)
    ):
        value = located.state.value
        return value.point if isinstance(value, Value) else value
    return None
