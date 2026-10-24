"""Source oxygen conditions for Knudsen comparisons (owner decision d-216)."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Iterable, Mapping

from simulator.battery.enums import AdmissionStatus, EvidenceClass, NoticeKind, Phase, Quantity, UncertaintyKind, ValueKind
from simulator.battery.identity import Identity, quantity_token
from simulator.battery.records import Bench, Derivation, Located, Locator, Notice, Observation, Species, State, Uncertainty, Value, _is_source_internally_inconsistent
from simulator.battery.waypoints import identity_composition_waypoint
from simulator.physical_constants import GAS_CONSTANT

SOURCE_OXYGEN_RELATION = "source_oxygen_condition;"
CO_MEASURED_CONSISTENCY_ORIGIN = "co-measured oxygen consistency"


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


def _printed_source_oxygen_at_point(
    reference: Observation, observations: Iterable[Observation],
) -> tuple[Decimal, tuple[str, ...]] | SourceOxygenFallback | None:
    """One printed oxygen command at a physical point, independent of target column."""
    key = _point_key(reference)
    candidates = {}
    for row in (reference, *observations):
        if (
            row.source_id != reference.source_id or row.engine is not None
            or row.admission.status is not AdmissionStatus.ADMITTED
            or (row.observation_id != reference.observation_id and (key is None or _point_key(row) != key))
        ):
            continue
        pressure = printed_source_pressure(row)
        if pressure is not None:
            candidates[row.observation_id] = pressure
    if not candidates:
        return None
    pressures = set(candidates.values())
    if len(pressures) != 1:
        return SourceOxygenFallback(SourceOxygenFallbackReason.AMBIGUOUS)
    return pressures.pop(), tuple(sorted(candidates))


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


def _comeasured_oxygen_pair(
    measured: list[tuple[Observation, set[str]]],
) -> tuple[Observation, set[str], Observation, set[str]] | None:
    """One admitted measured O and one admitted measured O2, or nothing.

    ``measured`` is already the eligible measured_direct / measured_reduced
    set. Any other count or formula mix is not this condition.
    """
    if len(measured) != 2:
        return None
    by_formula: dict[str, tuple[Observation, set[str]]] = {}
    for row, used in measured:
        formula = row.identity.species.formula
        if formula in by_formula:
            return None
        by_formula[formula] = (row, used)
    if set(by_formula) != {"O", "O2"}:
        return None
    o_row, o_used = by_formula["O"]
    o2_row, o2_used = by_formula["O2"]
    return o_row, o_used, o2_row, o2_used


def _positive_oxygen_pressure(row: Observation) -> bool:
    """A printed O or O2 pressure. Locator and evidence are eligibility, not this."""
    return (
        row.identity.species.formula in {"O", "O2"}
        and row.value.kind is ValueKind.POINT
        and row.value.point is not None
        and row.value.point.is_finite()
        and row.value.point > 0
    )


def _printed_partner_is_ineligible(
    point: Mapping[str, Observation],
    measured: list[tuple[Observation, set[str]]],
    cell_oxygen: list[tuple[Observation, set[str]]],
) -> bool:
    """Both formulas are printed and this is not one eligible condition.

    Counted on the printed rows, before eligibility drops a partner.
    Exactly one measured-eligible side is ambiguous. Neither side eligible
    is ambiguous too: an O/O2 pair where either row fails, including both,
    stays AMBIGUOUS. A cell-route O is the base exception, and it still
    owns a downstream printed O2 linked to that cell row. A measured O2
    still precedes a cell-route O when every printed O is that cell row.
    """
    printed: dict[str, list[Observation]] = {"O": [], "O2": []}
    for row in point.values():
        if _positive_oxygen_pressure(row):
            printed[row.identity.species.formula].append(row)
    if not printed["O"] or not printed["O2"]:
        return False
    eligible: dict[str, set[str]] = {"O": set(), "O2": set()}
    for row, _used in measured:
        eligible[row.identity.species.formula].add(row.observation_id)
    if eligible["O"] and eligible["O2"]:
        return False
    if not eligible["O"] and not eligible["O2"]:
        return not cell_oxygen
    if eligible["O2"] and not eligible["O"]:
        cell_ids = {row.observation_id for row, _used in cell_oxygen}
        if cell_ids and all(row.observation_id in cell_ids for row in printed["O"]):
            return False
    return True


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


_RELATIVE_BASES = {"relative", "fraction"}
_PERCENT_BASES = {"percent", "%", "percentage"}
_DEX_BASES = {"dex"}
_PERCENT_UNIT_NAMES = {"%", "percent", "percentage"}
_PERCENT_IN_PROSE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", re.IGNORECASE)
# Plural: Shornikov 2018 states "Table 1 pressures have an error no greater than 8%".
_PRESSURE_WORD = re.compile(r"\b(?:k\s+)?pressures?\b|\bp[_\s]?k\b", re.IGNORECASE)


def _finite_decimal(raw: object) -> Decimal | None:
    if isinstance(raw, bool) or isinstance(raw, (Mapping, list, tuple)):
        return None
    try:
        value = raw if isinstance(raw, Decimal) else Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return value if value.is_finite() else None


def _relative_dex(epsilon: Decimal) -> Decimal | None:
    """Dex half-envelope of a relative pressure width ε = δp/p.

    The downward side is wider, so the bound is -log10(1-ε) for
    0 <= ε < 1. ε = 0 is a stated exact value. ε >= 1 reaches a
    non-positive pressure and is not a finite dex bound.
    """
    if not epsilon.is_finite() or epsilon < 0:
        return None
    if epsilon == 0:
        return Decimal(0)
    if epsilon >= 1:
        return None
    return -((Decimal(1) - epsilon).ln() / Decimal(10).ln())


def _width_magnitude(value: Decimal | tuple[Decimal, Decimal]) -> Decimal:
    if isinstance(value, tuple):
        return max(abs(value[0]), abs(value[1]))
    return abs(value)


def _pressure_range_dex(
    pressure_pa: Decimal,
    bounds: tuple[Decimal, Decimal],
    basis: str,
) -> Decimal | None:
    from simulator.battery.migrate import convert_pressure_to_pa

    low, _low_why = convert_pressure_to_pa(bounds[0], basis)
    high, _high_why = convert_pressure_to_pa(bounds[1], basis)
    if low is None or high is None or pressure_pa <= 0 or low <= 0 or high <= 0:
        return None
    low_dex = abs((pressure_pa / low).ln() / Decimal(10).ln())
    high_dex = abs((high / pressure_pa).ln() / Decimal(10).ln())
    return max(low_dex, high_dex)


def _dex_from_stated_value(
    value: Decimal | tuple[Decimal, Decimal],
    basis: str,
    pressure_pa: Decimal,
) -> Decimal | None:
    key = basis.strip().casefold()
    if key in _DEX_BASES:
        width = _width_magnitude(value)
        return width if width.is_finite() and width >= 0 else None
    if key in _RELATIVE_BASES:
        return _relative_dex(_width_magnitude(value))
    if key in _PERCENT_BASES:
        return _relative_dex(_width_magnitude(value) / Decimal(100))
    if isinstance(value, tuple):
        return _pressure_range_dex(pressure_pa, value, basis)
    from simulator.battery.migrate import convert_pressure_to_pa

    converted, _why = convert_pressure_to_pa(value, basis)
    if converted is None or pressure_pa <= 0:
        return None
    return _relative_dex(abs(converted) / pressure_pa)


def _percent_magnitudes(node: object, found: list[Decimal]) -> None:
    """Collect one magnitude per mapping that itself carries units=% and a number."""
    if isinstance(node, Mapping):
        units = None
        number = None
        for key, raw in node.items():
            name = str(key).casefold()
            if name in {"unit", "units", "basis_unit"} and isinstance(raw, str):
                if raw.strip().casefold() in _PERCENT_UNIT_NAMES:
                    units = raw
            if name in {"bound_value", "value"}:
                parsed = _finite_decimal(raw)
                if parsed is not None:
                    number = abs(parsed)
        if units is not None and number is not None:
            found.append(number)
        for raw in node.values():
            if isinstance(raw, (Mapping, list, tuple)):
                _percent_magnitudes(raw, found)
    elif isinstance(node, (list, tuple)):
        for raw in node:
            _percent_magnitudes(raw, found)


def _verbatim_text(verbatim: object) -> str | None:
    if verbatim is None:
        return None
    if isinstance(verbatim, str):
        return verbatim
    try:
        return json.dumps(verbatim, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(verbatim)


def _dex_from_verbatim(verbatim: object) -> Decimal | None:
    found: list[Decimal] = []
    _percent_magnitudes(verbatim, found)
    if len(found) == 1:
        return _relative_dex(found[0] / Decimal(100))
    text = _verbatim_text(verbatim)
    if not text:
        return None
    for match in _PERCENT_IN_PROSE.finditer(text):
        start = max(0, match.start() - 80)
        window = text[start : min(len(text), match.end() + 80)]
        if _PRESSURE_WORD.search(window) is None:
            continue
        return _relative_dex(Decimal(match.group(1)) / Decimal(100))
    return None


def _stated_pressure_dex(uncertainty: Uncertainty, pressure_pa: Decimal) -> Decimal | None:
    """Usable stated dex envelope, or None when the source stated nothing usable.

    A typed value and basis win over verbatim. ``uncertainty_for`` often
    stores the extract mapping as PRINTED verbatim with value left empty;
    that payload is still the typed verbatim field.
    """
    if uncertainty.kind is UncertaintyKind.NONE:
        return None
    if uncertainty.kind not in {UncertaintyKind.PRINTED, UncertaintyKind.DERIVED}:
        return None
    if uncertainty.value is not None:
        if not uncertainty.basis:
            return None
        return _dex_from_stated_value(uncertainty.value, uncertainty.basis, pressure_pa)
    return _dex_from_verbatim(uncertainty.verbatim)


def _combined_stated_consistency_dex(
    atomic: Observation, molecular: Observation
) -> Decimal | None:
    """Combined stated uncertainty of log10(pO2_printed / pO2_from_O), in dex.

    Premise. JANAF O-001 is 1/2 O2 = O, so at fixed T the K_O-implied
    pO2 is proportional to pO squared. K_O is the compilation constant,
    not a stated source uncertainty, and is left out of the envelope.
    The ruling does not establish that the two readings are independent:
    one may be reduced from the other. The bound is still the sum of
    the envelopes, not a root-sum-square, so the envelopes can line up.
    That sum is the notice threshold. It is not a claim that the
    readings were made separately.

    Algebra. consistency_dex = log10(pO2) - 2 log10(pO) - constant(T).
    A relative width ε moves log10(p) by at most -log10(1-ε) when
    0 < ε < 1. The atomic pressure is squared, so its dex envelope is
    doubled. Combined dex = u(pO2) + 2 u(pO). A side with no usable
    stated width contributes nothing. It is not treated as zero, and
    it does not invent a threshold. If neither side yields a finite
    envelope, there is no combined bound.

    Unit check. ε = δp/p is dimensionless. -log10(1-ε) is dex. The
    factor 2 is dimensionless: log10(p^2) = 2 log10(p), and twice a
    dex envelope is dex. The sum is dex. consistency_dex is
    log10(Pa/Pa) = dex.

    Sanity. Shornikov at 1933 K, O = 2.65e-9 atm and O2 = 4.59e-11 atm,
    gives |consistency_dex| about 0.0016. A stated ±8% on both sides is
    u = -log10(0.92) ≈ 0.03621 dex, combined ≈ 0.1086 dex, and the
    0.0016 dex gap does not exceed it. A stated relative width of 1e-8
    on either side is about 4e-9 dex, and the same gap does exceed it.
    """
    atomic_dex = _stated_pressure_dex(atomic.uncertainty, atomic.value.point)
    molecular_dex = _stated_pressure_dex(molecular.uncertainty, molecular.value.point)
    if atomic_dex is None and molecular_dex is None:
        return None
    combined = Decimal(0)
    if molecular_dex is not None:
        combined += molecular_dex
    if atomic_dex is not None:
        combined += Decimal(2) * atomic_dex
    return combined


def _co_measured_consistency_notice(
    atomic: Observation, molecular: Observation, consistency_dex: Decimal
) -> Notice | None:
    combined = _combined_stated_consistency_dex(atomic, molecular)
    if combined is None or abs(consistency_dex) <= combined:
        return None
    # SOURCE_DISAGREEMENT without the source_internally_inconsistent
    # prefix: the pair is still one condition. The prefix would block
    # selection and open a flagged stratum. No other NoticeKind is a
    # stated-width disagreement between two printed pressures.
    return Notice(
        kind=NoticeKind.SOURCE_DISAGREEMENT,
        affected_quantities=(Quantity.P_PARTIAL,),
        reason=(
            "co_measured_o_o2_consistency: "
            f"|consistency_dex|={abs(consistency_dex)} exceeds combined stated uncertainty {combined} dex"
        ),
        origin=CO_MEASURED_CONSISTENCY_ORIGIN,
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
    comeasured = _comeasured_oxygen_pair(measured)
    if comeasured is not None:
        o_row, o_used, o2_row, o2_used = comeasured
        source, used = o2_row, o_used | o2_used
    elif _printed_partner_is_ineligible(point, measured, cell_oxygen):
        return SourceOxygenFallback(SourceOxygenFallbackReason.AMBIGUOUS)
    elif len(candidates) != 1:
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
    else:
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
    params: list[tuple[str, Located]] = [
        ("source_pressure_Pa", Located(State.of(source.value.point), locator=source.locator))
    ]
    consistency = None
    if comeasured is not None:
        pressure = source.value.point
        arithmetic = "printed pO2 in Pa (recorded source-unit conversion)"
        temperature = reference.identity.temperature_K.value
        gibbs, constant, janaf = _janaf_atomic_oxygen(temperature)
        from_o = _po2_pa_from_atomic_oxygen(o_row.value.point, constant)
        consistency_dex = (pressure / from_o).ln() / Decimal(10).ln()
        params.extend(
            _atomic_oxygen_equilibrium_parameters(
                temperature, o_row.locator, gibbs, constant, janaf
            )
        )
        params.extend((
            ("pO2_from_O_Pa", Located(State.of(from_o), locator=janaf)),
            ("consistency_dex", Located(State.of(consistency_dex), locator=janaf)),
        ))
        consistency = _co_measured_consistency_notice(o_row, source, consistency_dex)
    elif source.identity.species.formula == "O":
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
        notices=(*source.notices, consistency) if consistency is not None else source.notices,
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
