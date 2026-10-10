"""Compilation-tier scoring: series cells as points, engine thermochemistry.

Evaluated compilations stay COMPILATION_ASSESSED. Printed (T, value) pairs
are scored in place. They are not written back as new store rows: a JANAF
slice measured 2026-09-25 was 53,135 JSON bytes for 769 cells and about
2.46 MB if each cell became its own observation (46×). At that ratio the
574,790 banded cells are on the order of 1.8 GB on top of the existing
observation store.

A series point's identity is ``{observation_id}#t{T}v{value}`` with an
``n{occurrence}`` suffix only when the same temperature and value repeat.
Reordering the stored pairs does not rename a printed point. Intervals
are not collapsed. ``transition_temperature`` series are not expanded.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field, replace
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from simulator.battery.enums import (
    EQUILIBRIUM_FIT_QUANTITIES,
    FORMATION_QUANTITIES,
    PURE_STANDARD_THERMO,
    QUANTITY_UNITS,
    Authority,
    Engine,
    EvidenceClass,
    IdentityEqualKind,
    NoticeKind,
    PerBasis,
    Phase,
    Quantity,
    RefusalReason,
    MetricOperation,
    ResidualStatus,
    SourceRelation,
    ValueKind,
)
from simulator.battery.identity import (
    Identity,
    identity_equal,
    log10K_from_delta_fG_kJ_mol,
    quantity_token,
    standard_pressure_delta_g_kJ_per_mol,
)
from simulator.battery.records import (
    DecisionBand,
    Notice,
    Observation,
    State,
    Uncertainty,
    Value,
    as_decimal,
    phase_token,
    polymorph_token,
)
from simulator.chemistry.ellingham_thermo import (
    ELLINGHAM_FIT_SEGMENTS,
    EllinghamFitSegment,
    ellingham_segment_for_temperature,
)

# ``-> [coeff ]Formula(token)``. Coeff defaults to 1 (``TiO2(rutile,s)``).
_PRODUCT_RE = re.compile(
    r"->\s*(?:([0-9]+(?:/[0-9]+)?)\s+)?([A-Za-z][A-Za-z0-9]*)\(([^)]+)\)"
)
_PHASED_SPECIES_RE = re.compile(r"([A-Za-z][A-Za-z0-9]*)\(([^)]+)\)")

# Berman symbols. The live pure-phase call returns the database formula;
# a disagreement is a refusal, not a scored residual. Polymorph labels
# stay on the engine module (_TE_PURE_PHASE_POLYMORPH).
_TE_FORMULA: dict[str, str] = {
    "Fo": "Mg2SiO4",
    "Fa": "Fe2SiO4",
    "Per": "MgO",
    "Qz": "SiO2",
    "Crs": "SiO2",
    "Trd": "SiO2",
    "En": "MgSiO3",
    "cEn": "MgSiO3",
    "pEn": "MgSiO3",
    "Lm": "CaO",
    "Crn": "Al2O3",
    "Spl": "MgAl2O4",
    "And": "Al2SiO5",
    "Ky": "Al2SiO5",
    "Sil": "Al2SiO5",
}

_THERMO_QUANTITIES = FORMATION_QUANTITIES | EQUILIBRIUM_FIT_QUANTITIES | PURE_STANDARD_THERMO
_ENGINE_ELLINGHAM_QUANTITIES = frozenset(
    {Quantity.DELTA_FG, Quantity.LOG10_KF}
)
_ENGINE_PURE_PHASE_QUANTITIES = frozenset(
    {Quantity.CP, Quantity.S, Quantity.H_MINUS_H298}
)
_ENGINE_THERMO_QUANTITIES: dict[Engine, frozenset[Quantity]] = {
    Engine.INTERNAL_ANALYTICAL: _ENGINE_ELLINGHAM_QUANTITIES,
    Engine.VAPOROCK: _ENGINE_PURE_PHASE_QUANTITIES,
    Engine.THERMOENGINE: _ENGINE_PURE_PHASE_QUANTITIES,
    Engine.MAGEMIN: _ENGINE_PURE_PHASE_QUANTITIES,
}
_H298_K = 298.15
_PA_PER_BAR = Decimal("100000")


def _engine_thermo_quantities(engine: Engine) -> frozenset[Quantity]:
    return _ENGINE_THERMO_QUANTITIES.get(engine, frozenset())


@dataclass(frozen=True)
class _Product:
    formula: str
    coeff: Fraction
    phase: Phase
    polymorph: str | None  # None = generic crystal or a fluid
    token: str


@dataclass(frozen=True)
class ThermoAttempt:
    """One engine thermochemistry attempt. ``value is None`` is a refusal.

    Never a placeholder 0. ``call_evidence`` names the segment or phase.
    """

    value: Decimal | None
    unit: str | None
    authority: Authority
    notices: tuple[Notice, ...]
    refusal_reason: RefusalReason | None
    refusal_detail: dict[str, object]
    call_evidence: str


def _decimal_token(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"", "-", "-0"}:
        return "0"
    return text


def _is_decimal_token(text: str) -> bool:
    body = text[1:] if text.startswith("-") else text
    if not body or body.count(".") > 1:
        return False
    return body.replace(".", "").isdigit()


def _is_point_suffix(suffix: str) -> bool:
    if not suffix.startswith("t"):
        return False
    body = suffix[1:]
    head, sep, tail = body.partition("v")
    if not sep or not head:
        return False
    if "n" in tail:
        value, mark, occurrence = tail.rpartition("n")
        if not mark or not occurrence.isdigit():
            return False
        tail = value
    return _is_decimal_token(head) and _is_decimal_token(tail)


def compilation_point_id(
    observation_id: str,
    temperature: Decimal,
    magnitude: Decimal,
    occurrence: int = 0,
) -> str:
    """Stable id of one printed series cell. Not its stored position."""

    token = f"t{_decimal_token(temperature)}v{_decimal_token(magnitude)}"
    if occurrence:
        token = f"{token}n{occurrence}"
    return f"{observation_id}#{token}"


def parent_observation_id(reference_id: str) -> str:
    """Series-point ids end in ``#<index>`` or ``#t<T>v<value>``.

    Anything else is itself. No stored observation id contains ``#``.
    """

    parent, sep, suffix = reference_id.rpartition("#")
    if sep and (suffix.isdigit() or _is_point_suffix(suffix)):
        return parent
    return reference_id


def is_compilation_evidence(observation: Observation) -> bool:
    evidence = observation.evidence.class_
    return bool(
        evidence.is_value and evidence.value is EvidenceClass.COMPILATION_ASSESSED
    )


def _score_predicates():
    from simulator.battery.score import (
        is_compilation_source,
        is_internal_consistency,
        is_sf04_workbook,
        parse_species_formula,
    )

    return is_compilation_source, is_internal_consistency, is_sf04_workbook, parse_species_formula


def compilation_series_points(
    observation: Observation,
    origin: str | None = None,
) -> tuple[Observation, ...]:
    """Printed series cells as point observations. Store rows are not copied.

    One point per stored pair. No midpoint. ``transition_temperature``
    series, internal-consistency ledgers, and the SF04 workbook stay as
    they are. Empirical series are not compilation series.
    """

    return tuple(_iter_compilation_series_points(observation, origin))


def _compilation_series_pairs(
    observation: Observation,
    origin: str | None,
) -> tuple[tuple[object, object], ...] | None:
    is_compilation_source, is_internal_consistency, is_sf04_workbook, _parse = (
        _score_predicates()
    )
    if is_internal_consistency(origin) or is_sf04_workbook(observation):
        return None
    compilation = is_compilation_evidence(observation) or is_compilation_source(
        observation.source_id, origin
    )
    value = observation.value
    if (
        not compilation
        or value.kind is not ValueKind.SERIES
        or not value.series
        or not isinstance(observation.identity, Identity)
        or quantity_token(observation.identity) is Quantity.TRANSITION_TEMPERATURE
    ):
        return None
    return value.series


def _compilation_series_point_count(
    observation: Observation,
    origin: str | None = None,
) -> int:
    pairs = _compilation_series_pairs(observation, origin)
    return 1 if pairs is None else len(pairs)


def _iter_compilation_series_points(
    observation: Observation,
    origin: str | None = None,
) -> Iterable[Observation]:
    pairs = _compilation_series_pairs(observation, origin)
    if pairs is None:
        yield observation
        return
    seen: dict[str, int] = {}
    for temperature, magnitude in pairs:
        temp_d = as_decimal(temperature)
        mag_d = as_decimal(magnitude)
        key = f"{_decimal_token(temp_d)}|{_decimal_token(mag_d)}"
        occurrence = seen.get(key, 0)
        seen[key] = occurrence + 1
        identity = replace(
            observation.identity,
            temperature_K=State.of(temp_d),
        )
        yield replace(
            observation,
            observation_id=compilation_point_id(
                observation.observation_id, temp_d, mag_d, occurrence
            ),
            identity=identity,
            value=Value.point_of(mag_d),
            derived_from=(observation.observation_id,)
            + tuple(observation.derived_from or ()),
        )


def _state_value(state: object) -> object | None:
    if isinstance(state, State):
        return state.value if state.is_value else None
    return state


def _per_basis(identity: Identity) -> PerBasis | None:
    value = _state_value(identity.per)
    return value if isinstance(value, PerBasis) else None


def _product_phase(token: str) -> tuple[Phase, str | None]:
    text = token.strip().lower()
    if "liquid" in text or text == "l":
        return Phase.L, None
    if text in {"g", "gas"}:
        return Phase.G, None
    specific = text[:-2] if text.endswith(",s") else text
    if specific in {"s", "cr", "c"}:
        return Phase.CR, None
    return Phase.CR, specific


def _parse_product(segment: EllinghamFitSegment) -> _Product | None:
    match = _PRODUCT_RE.search(segment.phase_basis)
    if match is None:
        return None
    coeff_text, formula, token = match.group(1), match.group(2), match.group(3)
    coeff = Fraction(1) if coeff_text is None else Fraction(coeff_text)
    if coeff != Fraction(segment.n_ox).limit_denominator(12) and abs(
        float(coeff) - float(segment.n_ox)
    ) > 1e-9:
        return None
    phase, polymorph = _product_phase(token)
    return _Product(formula, coeff, phase, polymorph, token.strip())


def _phase_compatible(product: _Product, identity: Identity) -> bool:
    if phase_token(identity.species) is not product.phase:
        return False
    if product.phase is not Phase.CR:
        return True
    identity_poly = polymorph_token(identity.species)
    identity_name = None if identity_poly is None else identity_poly.value
    if product.polymorph is None:
        return True
    if identity_name is None:
        return False
    return identity_name == product.polymorph


def _vaporock_gas_provenance(equil_module) -> str:
    """Identify the loaded JANAF table and checkout once per process."""
    cached = getattr(_vaporock_gas_provenance, "_identity", None)
    if cached is None:
        module_path = Path(equil_module.__file__).resolve()
        table_path = (
            module_path.parent / "data" / "JANAF-vapor-data-full.csv"
        ).resolve(strict=True)
        table_sha256 = hashlib.sha256(table_path.read_bytes()).hexdigest()
        checkout = module_path.parents[2]
        git_sha = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "-C", str(checkout), "status", "--porcelain"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
        )
        cached = (
            f"janaf_csv_path={table_path}:janaf_csv_sha256={table_sha256}:"
            f"vaporock_git_sha={git_sha}:vaporock_git_dirty={str(dirty).lower()}"
        )
        setattr(_vaporock_gas_provenance, "_identity", cached)
    return cached


def _refuse(
    reason: RefusalReason,
    detail: str,
    *,
    quantity: Quantity,
    origin: str,
    extra: dict[str, object] | None = None,
    call_evidence: str = "thermochemistry",
) -> ThermoAttempt:
    payload: dict[str, object] = {"reason": detail, "quantity": quantity.value}
    if extra:
        payload.update(extra)
    return ThermoAttempt(
        value=None,
        unit=None,
        authority=Authority.REFUSED,
        notices=(),
        refusal_reason=reason,
        refusal_detail=payload,
        call_evidence=call_evidence,
    )


def _ellingham_attempt(
    identity: Identity,
    quantity: Quantity,
    temperature_K: Decimal,
    *,
    origin: str,
) -> ThermoAttempt:
    """ΔfG and log10 Kf from the Ellingham reaction fit. Nothing else.

    The fit stores dG(T) = dH − T·dS in kJ per mol O2 for one reaction,
    ``n_M metal + O2 → n_ox oxide``, with the metal and oxide phases
    named on that segment. Dividing by n_ox is the oxide formation value
    only while those phases are the standard states, which is the
    segment's own temperature range. Outside that range the selector
    clamps to a neighbouring reaction. That number is not ΔfG and is
    refused, not scored as an extrapolated formation value.

    Unit check: (kJ/mol O2) / (mol oxide / mol O2) = kJ/mol oxide.
    log10 Kf = −ΔfG / (R T ln 10) with ΔfG in kJ/mol and R in kJ/(mol·K)
    (``log10K_from_delta_fG_kJ_mol``). Dimensionless.

    Sanity, JANAF Na-014 Na2O(l) at 2200 K, printed ΔfG° = −3.886 kJ/mol
    and log10 Kf = 0.092. The 2200 K segment is
    ``4 Na(g) + O2 → 2 Na2O(l)`` with dG = −7.4056 kJ/mol O2, so
    ΔfG = −7.4056 / 2 = −3.7028 kJ/mol. Residual +0.183 kJ/mol, inside
    the segment's 0.442 kJ/mol O2 construction bound (0.221 kJ/mol oxide).
    log10 Kf from −3.7028 kJ/mol is 0.0879, 0.004 from the printed 0.092.
    K2O at 298.15 K is outside the 1100 K K(g) segment and is refused.
    """

    if quantity not in _engine_thermo_quantities(Engine.INTERNAL_ANALYTICAL):
        return _refuse(
            RefusalReason.UNSUPPORTED,
            "ellingham-emits-reaction-dg-only",
            quantity=quantity,
            origin=origin,
        )
    per = _per_basis(identity)
    if per not in {PerBasis.MOL_SPECIES, PerBasis.MOL_O2}:
        return _refuse(
            RefusalReason.UNSUPPORTED,
            "per-basis-unsupported",
            quantity=quantity,
            origin=origin,
            extra={"per": None if per is None else per.value},
        )
    formula = identity.species.formula
    _is_comp, _is_internal, _is_sf04, parse_species_formula = _score_predicates()
    target = parse_species_formula(formula)
    if target is None:
        return _refuse(
            RefusalReason.IDENTITY_UNKNOWN,
            "species-formula-unparsed",
            quantity=quantity,
            origin=origin,
            extra={"formula": formula},
        )
    temperature = float(temperature_K)
    matches: list[tuple[str, EllinghamFitSegment, _Product, bool]] = []
    formula_hit = False
    for metal in ELLINGHAM_FIT_SEGMENTS:
        segment = ellingham_segment_for_temperature(metal, temperature)
        product = _parse_product(segment)
        if product is None:
            continue
        if parse_species_formula(product.formula) != target:
            continue
        formula_hit = True
        if not _phase_compatible(product, identity):
            continue
        low, high = segment.range_K
        extrapolated = not (low <= temperature <= high)
        matches.append((metal, segment, product, extrapolated))
    if not matches:
        return _refuse(
            RefusalReason.UNSUPPORTED,
            "ellingham-phase-or-polymorph-not-in-fit"
            if formula_hit
            else "engine-thermo-does-not-emit",
            quantity=quantity,
            origin=origin,
            extra={"formula": formula},
        )
    if len(matches) != 1:
        return _refuse(
            RefusalReason.UNSUPPORTED,
            "ellingham-oxide-ambiguous",
            quantity=quantity,
            origin=origin,
            extra={"formulas": [item[0] for item in matches]},
        )
    metal, segment, product, extrapolated = matches[0]
    call = f"ellingham:{metal}:{product.token}:T={temperature_K}"
    if product.polymorph is None and product.phase is Phase.CR:
        call += ":generic-solid"
    if extrapolated:
        # Neighbouring-segment reaction dG is a different reference state.
        return _refuse(
            RefusalReason.UNSUPPORTED,
            "ellingham-formation-outside-segment",
            quantity=quantity,
            origin=origin,
            extra={
                "segment_range_K": [segment.range_K[0], segment.range_K[1]],
                "temperature_K": str(temperature_K),
                "phase_basis": segment.phase_basis,
            },
            call_evidence=call + ":outside-segment",
        )

    # dG is kJ/mol O2. mol-species divides by n_ox (mol oxide per mol O2).
    dG = Decimal(str(segment.delta_g_kJ_per_mol_O2(temperature)))
    per_species = dG / (Decimal(product.coeff.numerator) / Decimal(product.coeff.denominator))
    gibbs = per_species if per is PerBasis.MOL_SPECIES else dG

    # Premise: this Ellingham fit is at 1 bar, while a compilation cell may
    # print another ideal-gas standard pressure. Its explicit formation
    # reaction supplies Δν_g, normalized below to the identity's declared
    # molar basis. Algebra: ΔfG°(p2)-ΔfG°(p1) = Δν_g R T ln(p2/p1).
    # log10 Kf is recomputed from that shifted ΔfG. S°_gas(p2)-S°_gas(p1)
    # = -R ln(p2/p1) (this attempt emits no entropy). Unit check: R·T is
    # J/mol, divided by 1000 gives kJ/mol; the log shift is dimensionless.
    # Sanity: CaO from Ca(s)+½O2(g)→CaO(cr) has Δν_g=-½, so at 1000 K
    # and 1 atm the ΔfG shift is -½·8.314·1000·ln(1.01325) ≈ -54.7 J/mol.
    pressure_state = identity.standard_pressure_Pa
    if (
        isinstance(pressure_state, State)
        and pressure_state.is_value
        and pressure_state.value is not None
    ):
        printed_pressure = as_decimal(pressure_state.value)
        if printed_pressure != _PA_PER_BAR:
            if printed_pressure <= 0:
                return _refuse(
                    RefusalReason.IDENTITY_UNKNOWN,
                    "standard-pressure-transform-pressure-nonpositive",
                    quantity=quantity,
                    origin=origin,
                )
            reaction_state = identity.reaction
            reaction = (
                reaction_state.value
                if isinstance(reaction_state, State) and reaction_state.is_value
                else None
            )
            reaction_phases = (
                tuple(phase_token(term.species) for term in reaction.terms)
                if reaction is not None
                else ()
            )
            if reaction is not None and all(
                phase is not None for phase in reaction_phases
            ):
                segment_reactant_phases = {
                    formula: _product_phase(token)[0]
                    for formula, token in _PHASED_SPECIES_RE.findall(
                        segment.phase_basis.split("->", 1)[0]
                    )
                    if formula != "O2"
                }
                for term, cell_phase in zip(reaction.terms, reaction_phases):
                    if term.coefficient >= 0 or term.species.formula == "O2":
                        continue
                    segment_phase = segment_reactant_phases.get(term.species.formula)
                    if segment_phase is not cell_phase:
                        return _refuse(
                            RefusalReason.IDENTITY_MISMATCH,
                            "identity-mismatch-element-reference-phase",
                            quantity=quantity,
                            origin=origin,
                            extra={
                                "element": term.species.formula,
                                "cell_phase": cell_phase.value,
                                "segment_phase": (
                                    None if segment_phase is None else segment_phase.value
                                ),
                            },
                        )
            if reaction is None:
                return _refuse(
                    RefusalReason.IDENTITY_UNKNOWN,
                    "standard-pressure-transform-reaction-unknown",
                    quantity=quantity,
                    origin=origin,
                )
            if any(phase is None for phase in reaction_phases):
                return _refuse(
                    RefusalReason.IDENTITY_UNKNOWN,
                    "standard-pressure-transform-phase-unknown",
                    quantity=quantity,
                    origin=origin,
                )
            delta_n_g = sum(
                (
                    term.coefficient
                    for term, phase in zip(reaction.terms, reaction_phases)
                    if phase is Phase.G
                ),
                Fraction(0),
            )
            if per is PerBasis.MOL_SPECIES:
                basis_coefficient = sum(
                    (
                        term.coefficient
                        for term in reaction.terms
                        if term.species == identity.species and term.coefficient > 0
                    ),
                    Fraction(0),
                )
            else:
                basis_coefficient = sum(
                    (
                        -term.coefficient
                        for term, phase in zip(reaction.terms, reaction_phases)
                        if term.species.formula == "O2"
                        and phase is Phase.G
                        and term.coefficient < 0
                    ),
                    Fraction(0),
                )
            if basis_coefficient <= 0:
                return _refuse(
                    RefusalReason.IDENTITY_UNKNOWN,
                    "standard-pressure-transform-reaction-basis-unknown",
                    quantity=quantity,
                    origin=origin,
                )
            delta_n_g /= basis_coefficient
            delta_n_g_decimal = Decimal(delta_n_g.numerator) / Decimal(delta_n_g.denominator)
            gibbs += standard_pressure_delta_g_kJ_per_mol(
                delta_n_g_decimal,
                temperature_K,
                _PA_PER_BAR,
                printed_pressure,
            )
    if quantity is Quantity.LOG10_KF:
        value = log10K_from_delta_fG_kJ_mol(gibbs, temperature_K)
        unit = QUANTITY_UNITS[Quantity.LOG10_KF]
    else:
        value = gibbs
        unit = QUANTITY_UNITS[Quantity.DELTA_FG]
    notices: list[Notice] = []
    authority = Authority.CERTIFIED
    if segment.authority_status != "authoritative":
        authority = Authority.EXTRAPOLATED
        notices.append(
            Notice(
                kind=NoticeKind.OUT_OF_CERTIFIED_BAND,
                affected_quantities=(quantity,),
                reason=(
                    f"Ellingham segment {segment.range_K} "
                    f"authority {segment.authority_status}"
                ),
                origin=origin,
                band=str(segment.range_K),
            )
        )
    return ThermoAttempt(
        value=value,
        unit=unit,
        authority=authority,
        notices=tuple(notices),
        refusal_reason=None,
        refusal_detail={},
        call_evidence=call,
    )


def _standard_pressure_bar(identity: Identity) -> tuple[float | None, str | None]:
    state = identity.standard_pressure_Pa
    if isinstance(state, State) and state.is_value and state.value is not None:
        return float(as_decimal(state.value) / _PA_PER_BAR), None
    return None, "standard-pressure-unknown"


def _poly_name(identity: Identity) -> str | None:
    poly = polymorph_token(identity.species)
    return None if poly is None else poly.value


def _same_mineral(engine_poly: str | None, identity_poly: str | None) -> bool:
    if identity_poly is None or engine_poly is None:
        return False
    if engine_poly == identity_poly:
        return True
    from simulator.melt_backend.pure_phase_janaf_score import SAME_MINERAL_SUBFORMS

    covered = SAME_MINERAL_SUBFORMS.get(engine_poly)
    return bool(covered and identity_poly in covered)


def _pure_phase_rows(engine: Engine) -> tuple[tuple[str, str, str | None], ...] | None:
    """(symbol, formula, polymorph) from the engine's own map. None if unread."""

    try:
        if engine is Engine.MAGEMIN:
            from simulator.melt_backend.magemin import _MAGEMIN_PURE_PHASES

            return tuple(
                (symbol, spec.formula, spec.polymorph)
                for symbol, spec in _MAGEMIN_PURE_PHASES.items()
            )
        if engine is Engine.THERMOENGINE:
            from engines.alphamelts.thermoengine import _TE_PURE_PHASE_POLYMORPH

            rows = []
            for symbol, formula in _TE_FORMULA.items():
                rows.append((symbol, formula, _TE_PURE_PHASE_POLYMORPH.get(symbol)))
            return tuple(rows)
    except Exception as exc:  # noqa: BLE001 - missing engine module is a refusal
        _pure_phase_rows.failure = f"{type(exc).__name__}: {exc}"  # type: ignore[attr-defined]
        return None
    return ()


def _resolve_symbol(engine: Engine, identity: Identity) -> tuple[str | None, str]:
    if phase_token(identity.species) is not Phase.CR:
        return None, "pure-phase-is-crystal-only"
    rows = _pure_phase_rows(engine)
    if rows is None:
        detail = getattr(_pure_phase_rows, "failure", "unread")
        return None, f"pure-phase-map-unavailable:{detail}"
    _is_comp, _is_internal, _is_sf04, parse_species_formula = _score_predicates()
    target = parse_species_formula(identity.species.formula)
    if target is None:
        return None, "species-formula-unparsed"
    identity_poly = _poly_name(identity)
    candidates = [
        (symbol, poly)
        for symbol, formula, poly in rows
        if parse_species_formula(formula) == target
    ]
    if not candidates:
        return None, "pure-phase-symbol-unmapped"
    if identity_poly is not None:
        matched = [
            symbol
            for symbol, poly in candidates
            if poly == identity_poly or _same_mineral(poly, identity_poly)
        ]
    elif len(candidates) == 1:
        matched = [candidates[0][0]]
    else:
        matched = []
    if len(matched) == 1:
        return matched[0], ""
    if not matched:
        return None, "pure-phase-polymorph-unresolved"
    return None, "pure-phase-symbol-ambiguous"


_BACKENDS: dict[Engine, object] = {}


def default_pure_phase(engine: Engine, symbol: str, temperature_K: float, pressure_bar: float):
    """Live pure-phase accessor. Raises PurePhaseAccessError when unavailable."""

    from simulator.melt_backend.pure_phase import PurePhaseAccessError

    backend = _BACKENDS.get(engine)
    if backend is None:
        if engine is Engine.MAGEMIN:
            from simulator.melt_backend.magemin import MAGEMinBackend

            backend = MAGEMinBackend()
            binary = os.environ.get("REGOLITH_MAGEMIN_BINARY")
            config = {"database": "ig", "warm_worker": False}
            if binary:
                config["binary_path"] = binary
            if not backend.initialize(config):
                raise PurePhaseAccessError(
                    backend.unavailable_reason() if hasattr(backend, "unavailable_reason") else "magemin-unavailable"
                )
        elif engine is Engine.THERMOENGINE:
            from simulator.melt_backend.thermoengine import ThermoEngineBackend

            backend = ThermoEngineBackend()
            if not backend.initialize({}):
                raise PurePhaseAccessError("thermoengine-unavailable")
        else:
            raise PurePhaseAccessError(f"no pure-phase accessor for {engine.value}")
        _BACKENDS[engine] = backend
    return backend.pure_phase_properties(  # type: ignore[attr-defined]
        symbol, temperature_K=temperature_K, pressure_bar=pressure_bar
    )


def _property_value(props: object, quantity: Quantity, temperature_K: float, second: object | None):
    """Cp and S as returned. H−H298 from two apparent enthalpies.

    Derivation: H_app(T) = ΔfH°298 + [H(T)−H(298.15)]. The formation
    term cancels in the difference, which is the printed enthalpy
    increment. ``enthalpy_increment_kJ_mol`` divides J by 1000.
    Unit check: (J/mol) / 1000 = kJ/mol. Cp and S stay J/(K·mol).
    A missing property is the engine's typed absence, never 0.
    """

    if quantity is Quantity.CP:
        return getattr(props, "Cp_J_K_mol", None), "Cp"
    if quantity is Quantity.S:
        return getattr(props, "S_J_K_mol", None), "S"
    if quantity is Quantity.H_MINUS_H298:
        if second is None:
            return None, "H"
        h_t = getattr(props, "H_J_mol", None)
        h_0 = getattr(second, "H_J_mol", None)
        if h_t is None or h_0 is None:
            return None, "H"
        from simulator.melt_backend.pure_phase_janaf_score import (
            enthalpy_increment_kJ_mol,
        )

        return enthalpy_increment_kJ_mol(h_t, h_0), "H"
    return None, quantity.value


def _pure_phase_attempt(
    engine: Engine,
    identity: Identity,
    quantity: Quantity,
    temperature_K: Decimal,
    *,
    origin: str,
    invoke_pure_phase: bool,
    pure_phase: Callable | None,
) -> ThermoAttempt:
    if quantity in {Quantity.DELTA_FG, Quantity.LOG10_KF}:
        return _refuse(
            RefusalReason.UNSUPPORTED,
            "apparent-gibbs-is-not-delta-fG",
            quantity=quantity,
            origin=origin,
            extra={"engine": engine.value},
        )
    if quantity is Quantity.DELTA_FH:
        return _refuse(
            RefusalReason.UNSUPPORTED,
            "apparent-enthalpy-is-not-delta-fH",
            quantity=quantity,
            origin=origin,
            extra={"engine": engine.value},
        )
    if quantity not in _engine_thermo_quantities(engine):
        return _refuse(
            RefusalReason.UNSUPPORTED,
            "engine-thermo-does-not-emit",
            quantity=quantity,
            origin=origin,
            extra={"engine": engine.value},
        )
    per = _state_value(identity.per)
    if per is not PerBasis.MOL_SPECIES:
        return _refuse(
            RefusalReason.UNSUPPORTED,
            "pure-phase-per-basis-mismatch",
            quantity=quantity,
            origin=origin,
            extra={
                "engine": engine.value,
                "expected_per": PerBasis.MOL_SPECIES.value,
                "actual_per": getattr(per, "value", None),
            },
        )
    if quantity is Quantity.H_MINUS_H298:
        anchor = _state_value(identity.subtype)
        expected_anchor = "H(T)-H(298.15 K)"
        if anchor != expected_anchor:
            return _refuse(
                RefusalReason.UNSUPPORTED,
                "pure-phase-enthalpy-anchor-mismatch",
                quantity=quantity,
                origin=origin,
                extra={
                    "engine": engine.value,
                    "expected_anchor": expected_anchor,
                    "actual_anchor": anchor,
                },
            )
    symbol, why = _resolve_symbol(engine, identity)
    if symbol is None:
        reason = (
            RefusalReason.IDENTITY_UNKNOWN
            if why == "species-formula-unparsed"
            else RefusalReason.UNSUPPORTED
        )
        return _refuse(reason, why, quantity=quantity, origin=origin, extra={"engine": engine.value})
    if not invoke_pure_phase:
        return _refuse(
            RefusalReason.NOT_PROBED,
            "pure-phase-call-required",
            quantity=quantity,
            origin=origin,
            extra={"engine": engine.value, "symbol": symbol},
        )
    pressure_bar, pressure_reason = _standard_pressure_bar(identity)
    if pressure_bar is None:
        return _refuse(
            RefusalReason.IDENTITY_UNKNOWN,
            pressure_reason or "standard-pressure-unknown",
            quantity=quantity,
            origin=origin,
        )
    caller = pure_phase or default_pure_phase
    from simulator.melt_backend.pure_phase import PurePhaseAccessError

    try:
        props = caller(engine, symbol, float(temperature_K), pressure_bar)
        second = None
        if quantity is Quantity.H_MINUS_H298 and abs(float(temperature_K) - _H298_K) > 1e-6:
            second = caller(engine, symbol, _H298_K, pressure_bar)
        elif quantity is Quantity.H_MINUS_H298:
            second = props
    except PurePhaseAccessError as exc:
        return _refuse(
            RefusalReason.ATTEMPTED_UNAVAILABLE,
            "pure-phase-unavailable",
            quantity=quantity,
            origin=origin,
            extra={"engine": engine.value, "detail": str(exc)},
        )
    except (ImportError, ValueError, OSError, RuntimeError) as exc:
        return _refuse(
            RefusalReason.ATTEMPTED_UNAVAILABLE,
            "pure-phase-unavailable",
            quantity=quantity,
            origin=origin,
            extra={"engine": engine.value, "detail": str(exc)},
        )
    returned = getattr(props, "formula", None)
    _is_comp, _is_internal, _is_sf04, parse_species_formula = _score_predicates()
    if returned and parse_species_formula(str(returned)) != parse_species_formula(identity.species.formula):
        return _refuse(
            RefusalReason.IDENTITY_MISMATCH,
            "pure-phase-formula-disagrees",
            quantity=quantity,
            origin=origin,
            extra={"engine": engine.value, "returned": str(returned)},
        )
    raw, prop_name = _property_value(props, quantity, float(temperature_K), second)
    if raw is None:
        absence = ""
        for item in getattr(props, "absences", ()) or ():
            if getattr(item, "property", None) == prop_name:
                absence = str(getattr(item, "reason", ""))
                break
        return _refuse(
            RefusalReason.UNSUPPORTED,
            "pure-phase-property-absent",
            quantity=quantity,
            origin=origin,
            extra={"engine": engine.value, "property": prop_name, "absence": absence},
        )
    value = as_decimal(str(raw))
    if not value.is_finite():
        return _refuse(
            RefusalReason.METRIC_DOMAIN,
            "nonfinite-engine-value",
            quantity=quantity,
            origin=origin,
            extra={"engine": engine.value},
        )
    return ThermoAttempt(
        value=value,
        unit=QUANTITY_UNITS[quantity],
        authority=Authority.BRIDGE,
        notices=(),
        refusal_reason=None,
        refusal_detail={},
        call_evidence=f"pure-phase:{engine.value}:{symbol}:T={temperature_K}",
    )


def _vaporock_gas_attempt(
    identity: Identity,
    quantity: Quantity,
    temperature_K: Decimal,
    *,
    origin: str,
) -> ThermoAttempt:
    phase = identity.species.phase
    if (
        not isinstance(phase, State)
        or not phase.is_value
        or phase.value is not Phase.G
        or quantity not in _engine_thermo_quantities(Engine.VAPOROCK)
    ):
        return _refuse(
            RefusalReason.UNSUPPORTED,
            "engine-thermo-does-not-emit",
            quantity=quantity,
            origin=origin,
            extra={"engine": Engine.VAPOROCK.value},
        )
    if (
        not isinstance(identity.per, State)
        or not identity.per.is_value
        or identity.per.value is not PerBasis.MOL_SPECIES
    ):
        return _refuse(
            RefusalReason.IDENTITY_UNKNOWN,
            "vaporock-gas-thermo-requires-mol-species-basis",
            quantity=quantity,
            origin=origin,
        )

    charge = identity.species.charge
    if not isinstance(charge, State) or not charge.is_value:
        return _refuse(
            RefusalReason.IDENTITY_UNKNOWN,
            "vaporock-gas-charge-unknown",
            quantity=quantity,
            origin=origin,
        )
    if as_decimal(charge.value) != 0:
        return _refuse(
            RefusalReason.OUTSIDE_SUPPORTED_SPECIES,
            "vaporock-charged-species-not-in-janaf-table",
            quantity=quantity,
            origin=origin,
            extra={"engine": Engine.VAPOROCK.value, "charge": str(charge.value)},
        )

    try:
        vapor = getattr(_vaporock_gas_attempt, "_janaf_vapor", None)
        if vapor is None:
            from vaporock.equil import Vapor

            vapor = Vapor(database="JANAF")
            setattr(_vaporock_gas_attempt, "_janaf_vapor", vapor)
        import vaporock.equil as vaporock_equil

        provenance = _vaporock_gas_provenance(vaporock_equil)
    except Exception as exc:  # noqa: BLE001 - optional engine import boundary
        return _refuse(
            RefusalReason.ATTEMPTED_UNAVAILABLE,
            "vaporock-janaf-unavailable",
            quantity=quantity,
            origin=origin,
            extra={"engine": Engine.VAPOROCK.value, "detail": str(exc)},
            call_evidence="vaporock-janaf-provenance-unavailable",
        )

    formula = identity.species.formula
    species_name = formula if formula.endswith("(g)") else f"{formula}(g)"
    call_evidence = (
        f"vaporock-janaf-implementation-fidelity:{species_name}:"
        f"T={temperature_K}:{provenance}"
    )
    try:
        rows = vapor.vapor_coefs.loc[species_name]
    except KeyError:
        return _refuse(
            RefusalReason.OUTSIDE_SUPPORTED_SPECIES,
            "vaporock-species-not-in-janaf-table",
            quantity=quantity,
            origin=origin,
            extra={"engine": Engine.VAPOROCK.value, "species": species_name},
            call_evidence=call_evidence,
        )
    except Exception as exc:  # noqa: BLE001 - optional engine data boundary
        return _refuse(
            RefusalReason.ATTEMPTED_UNAVAILABLE,
            "vaporock-janaf-unavailable",
            quantity=quantity,
            origin=origin,
            extra={"engine": Engine.VAPOROCK.value, "detail": str(exc)},
            call_evidence=call_evidence,
        )

    table_rows = (
        (rows,)
        if getattr(rows, "ndim", 1) == 1
        else tuple(rows.iloc[index] for index in range(len(rows)))
    )
    temperature = float(temperature_K)
    matching_rows = tuple(
        row
        for row in table_rows
        if temperature > float(row["T_min"]) and temperature <= float(row["T_max"])
    )
    if not matching_rows:
        return _refuse(
            RefusalReason.UNSUPPORTED,
            "vaporock-temperature-outside-janaf-row",
            quantity=quantity,
            origin=origin,
            extra={"engine": Engine.VAPOROCK.value, "species": species_name},
            call_evidence=call_evidence,
        )
    if len(matching_rows) != 1:
        return _refuse(
            RefusalReason.IDENTITY_INCOMPLETE,
            "vaporock-janaf-interval-ambiguous",
            quantity=quantity,
            origin=origin,
            extra={"engine": Engine.VAPOROCK.value, "species": species_name},
            call_evidence=call_evidence,
        )
    row = matching_rows[0]

    # Shomate forms use t=T/1000: Cp=A+Bt+Ct²+Dt³+E/t² and
    # S=A ln(t)+Bt+Ct²/2+Dt³/3−E/(2t²)+G, both J/(mol·K);
    # H−H298=At+Bt²/2+Ct³/3+Dt⁴/4−E/t+F−H, in kJ/mol. Cp uses
    # the selected VapoRock row; its native evaluators supply S and
    # _janaf_dH's apparent H(T) terms through +F, not H−H298. Subtract the
    # selected row's H coefficient per the Shomate form. Unit check: kJ/mol
    # remains kJ/mol.
    # Sanity: K(g), 1200 K gives 18.7461926 kJ/mol vs printed JANAF
    # K-005 H−H298 = 18.746 kJ/mol.
    try:
        t = temperature / 1000.0
        if quantity is Quantity.CP:
            raw = (
                row["A"]
                + row["B"] * t
                + row["C"] * t**2
                + row["D"] * t**3
                + row["E"] / t**2
            )
        elif quantity is Quantity.S:
            raw = vapor._janaf_S(temperature, row)
        else:
            raw = vapor._janaf_dH(temperature, row) - row["H"]
        value = as_decimal(str(float(raw)))
    except Exception as exc:  # noqa: BLE001 - optional engine evaluator boundary
        return _refuse(
            RefusalReason.ATTEMPTED_UNAVAILABLE,
            "vaporock-janaf-evaluation-unavailable",
            quantity=quantity,
            origin=origin,
            extra={"engine": Engine.VAPOROCK.value, "detail": str(exc)},
            call_evidence=call_evidence,
        )
    if not value.is_finite():
        return _refuse(
            RefusalReason.METRIC_DOMAIN,
            "nonfinite-engine-value",
            quantity=quantity,
            origin=origin,
            extra={"engine": Engine.VAPOROCK.value},
            call_evidence=call_evidence,
        )
    return ThermoAttempt(
        value=value,
        unit=QUANTITY_UNITS[quantity],
        authority=Authority.BRIDGE,
        notices=(),
        refusal_reason=None,
        refusal_detail={},
        call_evidence=call_evidence,
    )


def predict_thermo_attempt(
    engine: Engine,
    observation: Observation,
    *,
    invoke_pure_phase: bool = True,
    pure_phase: Callable | None = None,
) -> ThermoAttempt:
    """Thermochemistry from that engine's own thermo, or a typed refusal.

    Internal-analytical uses the Ellingham fit (JANAF-4th refit).
    ThermoEngine and MAGEMin use pure-phase Cp, S, and H−H298.
    Apparent G is not ΔfG(T) and is refused. Other engines have no
    thermochemistry branch. A refusal carries no numeric value.
    """

    identity = observation.identity
    origin = observation.observation_id
    quantity = quantity_token(identity) if isinstance(identity, Identity) else None
    if not isinstance(identity, Identity) or quantity is None:
        return _refuse(
            RefusalReason.IDENTITY_UNKNOWN,
            "quantity-unknown",
            quantity=Quantity.DELTA_FG,
            origin=origin,
        )
    if quantity not in _THERMO_QUANTITIES:
        return _refuse(
            RefusalReason.UNSUPPORTED,
            "quantity-not-thermochemistry",
            quantity=quantity,
            origin=origin,
        )
    temperature = identity.temperature_K
    if not isinstance(temperature, State) or not temperature.is_value or temperature.value is None:
        return _refuse(
            RefusalReason.IDENTITY_UNKNOWN,
            "temperature-unknown",
            quantity=quantity,
            origin=origin,
        )
    temperature_K = as_decimal(temperature.value)
    if temperature_K <= 0:
        return _refuse(
            RefusalReason.IDENTITY_UNKNOWN,
            "temperature-not-positive",
            quantity=quantity,
            origin=origin,
        )
    if engine is Engine.INTERNAL_ANALYTICAL:
        return _ellingham_attempt(identity, quantity, temperature_K, origin=origin)
    if engine is Engine.VAPOROCK:
        return _vaporock_gas_attempt(identity, quantity, temperature_K, origin=origin)
    if engine in {Engine.THERMOENGINE, Engine.MAGEMIN}:
        return _pure_phase_attempt(
            engine,
            identity,
            quantity,
            temperature_K,
            origin=origin,
            invoke_pure_phase=invoke_pure_phase,
            pure_phase=pure_phase,
        )
    return _refuse(
        RefusalReason.UNSUPPORTED,
        "engine-thermo-does-not-emit",
        quantity=quantity,
        origin=origin,
        extra={"engine": engine.value},
    )


def uncertainty_text(uncertainty: Uncertainty) -> str:
    if uncertainty.verbatim is None and uncertainty.value is None:
        return uncertainty.kind.value
    return f"{uncertainty.kind.value}:{uncertainty.verbatim if uncertainty.verbatim is not None else uncertainty.value}"


def reference_observation(observations: Mapping[str, Observation], reference_id: str) -> Observation | None:
    found = observations.get(reference_id)
    if found is not None:
        return found
    return observations.get(parent_observation_id(reference_id))


def compilation_origin(
    reference_id: str, origins: Mapping[str, str] | None
) -> str | None:
    if not origins:
        return None
    return origins.get(reference_id) or origins.get(parent_observation_id(reference_id))


def compilation_row_observation(
    reference_id: str,
    observations: Mapping[str, Observation],
    origins: Mapping[str, str] | None = None,
) -> Observation | None:
    """Observation when this residual is compilation, else None.

    Assessed compilations and quoted rows stored under a compilation
    path are the same exclusion from the measured tier. Both belong in
    the compilation table.
    """

    from simulator.battery.score import is_compilation_source

    observation = reference_observation(observations, reference_id)
    if observation is None:
        return None
    origin = compilation_origin(reference_id, origins)
    if is_compilation_evidence(observation) or is_compilation_source(
        observation.source_id, origin
    ):
        return observation
    return None


def compilation_family(source_id: str | None, origin: str | None) -> str:
    if origin:
        family = origin.replace("\\", "/").split("/", 1)[0]
    else:
        family = source_id or "unknown"
    return family.removesuffix(".yaml")


def _median_abs(values: Sequence[Decimal]) -> str | None:
    if not values:
        return None
    ordered = sorted(abs(value) for value in values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return str(ordered[mid])
    return str((ordered[mid - 1] + ordered[mid]) / Decimal(2))


@dataclass(frozen=True)
class _TierCell:
    rail: str
    engine: str
    status: ResidualStatus
    relation: SourceRelation
    numeric: Decimal | None
    operation: MetricOperation | None
    unit: str
    family: str
    quantity: str
    uncertainty: str
    band_value: Decimal | str | None = None
    band_kind: str = "no_band"
    band_derived_n: int | None = None
    derive_eligible: bool = True


@dataclass
class _TierAggregate:
    count: int = 0
    refused: int = 0
    numeric_count: int = 0
    numeric_values: list[Decimal] = field(default_factory=list)
    same_source_count: int = 0
    independent_count: int = 0
    matched_same_source: int = 0
    matched_independent: int = 0
    no_band: int = 0
    band_values: set[str] = field(default_factory=set)
    band_kinds: set[str] = field(default_factory=set)
    derived_ns: set[int] = field(default_factory=set)
    matches_by_kind: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    mismatches_by_kind: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    uncertainty: str | None = None

    def add(self, cell: _TierCell, *, keep_values: bool) -> None:
        self.count += 1
        if self.uncertainty is None:
            self.uncertainty = cell.uncertainty
        self.refused += cell.status is ResidualStatus.REFUSED
        self.no_band += cell.status is ResidualStatus.NO_BAND
        if cell.numeric is None:
            return
        self.numeric_count += 1
        if keep_values:
            self.numeric_values.append(cell.numeric)
        if cell.relation in {SourceRelation.SAME_INPUT, SourceRelation.TRAINING}:
            self.same_source_count += 1
            if cell.status is ResidualStatus.MATCH:
                self.matched_same_source += 1
        elif cell.relation is SourceRelation.INDEPENDENT:
            self.independent_count += 1
            if cell.status is ResidualStatus.MATCH:
                self.matched_independent += 1
        if cell.band_value is not None:
            self.band_values.add(str(cell.band_value))
        self.band_kinds.add(cell.band_kind)
        if cell.band_derived_n is not None:
            self.derived_ns.add(cell.band_derived_n)
        if cell.status is ResidualStatus.MATCH:
            self.matches_by_kind[cell.band_kind] += 1
        elif cell.status is ResidualStatus.MISMATCH:
            self.mismatches_by_kind[cell.band_kind] += 1


class _TierMarkdownAccumulator:
    def __init__(
        self,
        typed_band_values: Mapping[
            tuple[str, str, str, str, str, str], set[str]
        ] | None = None,
    ) -> None:
        self.typed_band_values = (
            {} if typed_band_values is None else typed_band_values
        )
        self.by_engine: dict[str, _TierAggregate] = defaultdict(_TierAggregate)
        self.by_source: dict[tuple[str, str], _TierAggregate] = defaultdict(_TierAggregate)
        self.by_family_rail_engine_quantity: dict[
            tuple[str, str, str, str, str, str], _TierAggregate
        ] = defaultdict(_TierAggregate)
        self.derived_pool_n: dict[tuple[str, str, str, str], int] = defaultdict(int)

    def add(self, cell: _TierCell) -> None:
        self.by_engine[cell.engine].add(cell, keep_values=False)
        self.by_source[(cell.family, cell.quantity)].add(cell, keep_values=True)
        if (
            cell.numeric is not None
            and cell.operation is not None
            and cell.derive_eligible
        ):
            key = (
                cell.family,
                cell.rail,
                cell.engine,
                cell.relation.value,
                cell.quantity,
                cell.unit,
            )
            self.by_family_rail_engine_quantity[key].add(cell, keep_values=True)
            if cell.band_value is None:
                self.derived_pool_n[(cell.family, cell.quantity, cell.engine, cell.unit)] += 1


def _tier_markdown(
    cells: Iterable[_TierCell],
    *,
    typed_band_values: Mapping[tuple[str, str, str, str, str, str], set[str]] | None = None,
    accumulator: _TierMarkdownAccumulator | None = None,
) -> list[str]:
    from simulator.battery.score import (
        MIN_DERIVED_BAND_N,
        _bias_to_scatter_ratio,
        _median,
    )

    if accumulator is None:
        accumulator = _TierMarkdownAccumulator(typed_band_values)
        for cell in cells:
            accumulator.add(cell)
    by_engine = accumulator.by_engine
    by_source = accumulator.by_source
    by_family_rail_engine_quantity = accumulator.by_family_rail_engine_quantity
    derived_pool_n = accumulator.derived_pool_n
    typed_band_values = accumulator.typed_band_values
    lines = [
        "## Compilation tier",
        "",
        "Compilation comparisons: assessed tables and quoted rows stored",
        "under a compilation. Not part of the measured tier and not added",
        "to it. Same-source residuals measure implementation fidelity, not",
        "independent physics; the engine coefficients resolve to this",
        "compilation (JANAF-4th refit versus JANAF; NASA CEA thermo.inp",
        "versus the Glenn coefficient database). Pending admission is",
        "unchanged. Printed uncertainty is the reference observation's",
        "uncertainty (often none on a grid). The derived 2×MAD width measures",
        "scatter about the median, while membership is tested around zero:",
        "|residual| ≤ band width.",
        "",
        "| compilation | rail | engine | relation | quantity | unit | n | signed median residual | MAD | bias-to-scatter ratio | max |residual| | median abs residual | "
        "RMS residual | band value | band kind | band derived n | no-band reason | band-kind-specific matches / tail-in | "
        "band-kind-specific mismatches / tail-out | n no band | n same-source |",
        "|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|",
    ]
    if not by_family_rail_engine_quantity:
        lines.append(
            "| (none) | (none) | (none) | (none) | (none) | (none) | 0 | — | — | — | — | "
            "— | — | — | — | — | — | — | 0 | 0 | 0 | 0 |"
        )
    for (family, rail, engine, relation, quantity, unit), bucket in sorted(
        by_family_rail_engine_quantity.items()
    ):
        values = bucket.numeric_values
        rms = (
            sum((value * value for value in values), Decimal(0))
            / Decimal(len(values))
        ).sqrt()
        display_unit = (
            "kJ/mol" if unit == "kJ_per_declared_mol_basis" else unit
        )
        center = _median(values)
        mad = None if center is None else _median([abs(value - center) for value in values])
        ratio = _bias_to_scatter_ratio(center, mad, len(values))
        band_values = sorted(
            (typed_band_values or {}).get(
                (family, rail, engine, relation, quantity, unit),
                bucket.band_values,
            )
        )
        band_kinds = sorted(bucket.band_kinds)
        derived_ns = sorted(bucket.derived_ns)
        pool_n = derived_pool_n[(family, quantity, engine, unit)]
        if not derived_ns and bucket.no_band:
            derived_ns = [pool_n]
        match_display = "; ".join(
            f"{kind} {'tail-in' if kind == 'derived_2xMAD' else 'match'}={bucket.matches_by_kind[kind]}"
            for kind in band_kinds
        ) or "—"
        mismatch_display = "; ".join(
            f"{kind} {'tail-out' if kind == 'derived_2xMAD' else 'mismatch'}={bucket.mismatches_by_kind[kind]}"
            for kind in band_kinds
        ) or "—"
        no_band_reason = (
            "derived_band_insufficient_n"
            if 0 < pool_n < MIN_DERIVED_BAND_N
            and bucket.no_band
            else "—"
        )
        lines.append(
            f"| {family} | {rail} | {engine} | {relation} | {quantity} | {display_unit} | "
            f"{len(values)} | {center} | {mad} | {ratio if ratio is not None else '—'} | "
            f"{max((abs(value) for value in values), default=None)} | "
            f"{_median_abs(values)} | {rms} | {','.join(band_values) or '—'} | {','.join(band_kinds)} | {','.join(str(value) for value in derived_ns) or '—'} | {no_band_reason} | "
            f"{match_display} | {mismatch_display} | "
            f"{bucket.no_band} | "
            f"{bucket.same_source_count} |"
        )
    lines.extend(
        [
            "",
        "| engine | comparisons | refused | numeric | same-source | independent | match same-source | match independent |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    if not by_engine:
        lines.append("| (none) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |")
    for engine, bucket in sorted(by_engine.items()):
        lines.append(
            f"| {engine} | {bucket.count} | {bucket.refused} | {bucket.numeric_count} | "
            f"{bucket.same_source_count} | {bucket.independent_count} | "
            f"{bucket.matched_same_source} | {bucket.matched_independent} |"
        )
    lines.extend(
        [
            "",
            "| compilation | quantity | reachable | predicted | refused | median abs residual | uncertainty |",
            "|---|---|---:|---:|---:|---:|---|",
        ]
    )
    if not by_source:
        lines.append("| (none) |  | 0 | 0 | 0 | — |  |")
    for (family, quantity), bucket in sorted(by_source.items()):
        numeric_values = bucket.numeric_values
        lines.append(
            f"| `{family}` | `{quantity}` | {bucket.count} | {len(numeric_values)} | "
            f"{bucket.refused} | {_median_abs(numeric_values) or '—'} | "
            f"{bucket.uncertainty or ''} |"
        )
    lines.append("")
    return lines


def _cell_from_observation(
    *,
    engine: str,
    status: ResidualStatus,
    relation: SourceRelation,
    numeric: Decimal | None,
    operation: MetricOperation | None,
    unit: str,
    observation: Observation,
    origin: str | None,
    decision_band=None,
    derive_eligible: bool = True,
) -> _TierCell:
    quantity = "unknown"
    rail = "none"
    if isinstance(observation.identity, Identity):
        token = quantity_token(observation.identity)
        quantity = token.value if token is not None else "unknown"
        from simulator.battery.score import rail_for_quantity

        headline_rail = rail_for_quantity(
            token,
            species_formula=observation.identity.species.formula,
        )
        if headline_rail is not None:
            rail = headline_rail.value
    return _TierCell(
        rail=rail,
        engine=engine,
        status=status,
        relation=relation,
        numeric=numeric,
        operation=operation,
        unit=unit,
        family=compilation_family(observation.source_id, origin),
        quantity=quantity,
        uncertainty=uncertainty_text(observation.uncertainty),
        band_value=None if decision_band is None else decision_band.value,
        band_kind=(
            "no_band" if decision_band is None
            else "printed" if decision_band.rule == "source-printed per-cell uncertainty"
            else "derived_2xMAD" if "residual distribution" in decision_band.rule
            else "legacy_fallback"
        ),
        band_derived_n=(
            int(decision_band.rule.rsplit("derived_n=", 1)[1])
            if decision_band is not None and "derived_n=" in decision_band.rule
            else None
        ),
        derive_eligible=derive_eligible,
    )


def _tier_cell_from_payload(
    row: Mapping[str, object],
    *,
    reference: str,
    observation: Observation,
    origins: Mapping[str, str] | None,
    derive_eligible: bool,
) -> _TierCell:
    from simulator.battery.score import rail_for_quantity

    token = quantity_token(observation.identity) if isinstance(observation.identity, Identity) else None
    rail = "none"
    if isinstance(observation.identity, Identity):
        headline_rail = rail_for_quantity(
            token, species_formula=observation.identity.species.formula
        )
        if headline_rail is not None:
            rail = headline_rail.value
    return _tier_cell_from_payload_fields(
        row,
        family=compilation_family(
            observation.source_id, compilation_origin(reference, origins)
        ),
        quantity=token.value if token is not None else "unknown",
        rail=rail,
        uncertainty=uncertainty_text(observation.uncertainty),
        derive_eligible=derive_eligible,
    )


def _tier_cell_from_payload_fields(
    row: Mapping[str, object],
    *,
    family: str,
    quantity: str,
    rail: str,
    uncertainty: str,
    derive_eligible: bool,
    numeric_value: Decimal | None = None,
) -> _TierCell:
    request = row.get("candidate_request")
    engine = (
        str(request["engine"])
        if isinstance(request, Mapping) and request.get("engine")
        else str(row.get("key") or "").rsplit("::", 1)[-1]
    )
    raw_numeric = row.get("numeric")
    numeric = None
    operation = None
    if isinstance(raw_numeric, Mapping) and raw_numeric.get("value") is not None:
        numeric = (
            numeric_value
            if numeric_value is not None
            else as_decimal(raw_numeric["value"])
        )
        if raw_numeric.get("operation") is not None:
            operation = MetricOperation(str(raw_numeric["operation"]))
    unit = (
        str(raw_numeric.get("unit") or "")
        if isinstance(raw_numeric, Mapping)
        else ""
    )
    band_data = (
        raw_numeric.get("decision_band")
        if isinstance(raw_numeric, Mapping)
        else None
    )
    band = band_data if isinstance(band_data, Mapping) else None
    band_rule = "" if band is None else str(band.get("rule") or "")
    band_value = None if band is None else str(band["value"])
    return _TierCell(
        rail=rail,
        engine=engine or "unknown",
        status=ResidualStatus(str(row.get("status"))),
        relation=SourceRelation(
            str(row.get("source_relation") or SourceRelation.UNKNOWN.value)
        ),
        numeric=numeric,
        operation=operation,
        unit=unit,
        family=family,
        quantity=quantity,
        uncertainty=uncertainty,
        band_value=band_value,
        band_kind=(
            "no_band" if band is None
            else "printed" if band_rule == "source-printed per-cell uncertainty"
            else "derived_2xMAD" if "residual distribution" in band_rule
            else "legacy_fallback"
        ),
        band_derived_n=(
            int(band_rule.rsplit("derived_n=", 1)[1])
            if "derived_n=" in band_rule
            else None
        ),
        derive_eligible=derive_eligible,
    )


def compilation_tier_lines(
    residuals: Iterable[object],
    observations: Mapping[str, Observation],
    origins: Mapping[str, str] | None = None,
) -> list[str]:
    """Compilation tier beside the measured tier. The two counts are not added."""

    from simulator.battery.score import (
        Residual,
        _is_flagged_stratum_notice,
        _is_fusion_conversion_notice,
    )

    def cells() -> Iterable[_TierCell]:
        for residual in residuals:
            if not isinstance(residual, Residual):
                continue
            observation = compilation_row_observation(
                residual.reference, observations, origins
            )
            if observation is None:
                continue
            yield _cell_from_observation(
                engine=residual.key.rsplit("::", 1)[-1],
                status=residual.status,
                relation=residual.source_relation,
                numeric=None if residual.numeric is None else residual.numeric.value,
                operation=None
                if residual.numeric is None
                else residual.numeric.operation,
                unit="" if residual.numeric is None else residual.numeric.unit,
                observation=observation,
                origin=compilation_origin(residual.reference, origins),
                decision_band=None if residual.numeric is None else residual.numeric.decision_band,
                derive_eligible=not any(
                    _is_flagged_stratum_notice(notice)
                    or _is_fusion_conversion_notice(notice)
                    for notice in residual.notices
                ),
            )

    return _tier_markdown(cells())


def compilation_tier_lines_from_payloads(
    rows: Iterable[Mapping[str, object]],
    observations: Mapping[str, Observation],
    origins: Mapping[str, str] | None = None,
) -> list[str]:
    """Same compilation table from a residuals.jsonl payload."""

    from simulator.battery.score import _flagged_payload_strata

    def cells() -> Iterable[_TierCell]:
        for row in rows:
            reference = str(row.get("reference") or "")
            observation = compilation_row_observation(reference, observations, origins)
            if observation is None:
                continue
            yield _tier_cell_from_payload(
                row,
                reference=reference,
                observation=observation,
                origins=origins,
                derive_eligible=not bool(_flagged_payload_strata(row)),
            )

    return _tier_markdown(
        cells(),
        typed_band_values=getattr(rows, "_typed_tier_band_values", None),
    )


def write_compilation_comparisons_jsonl(context, path: Path) -> int:
    """Write per-point NASA-9 and Ellingham comparisons from their tier scores.

    The legacy channel scorers own the comparison arithmetic, status, and
    band. This writer streams those score objects to an additive JSONL file;
    it does not derive a second residual or decision.
    """

    from simulator.battery.score import (
        is_compilation_source,
        is_internal_consistency,
        is_sf04_workbook,
    )
    from simulator.diagnostic_helpers.species_rail_differential import (
        COMPILATION_JANAF,
        KeyedTablePoint,
        _compilation_comparison_key,
        classify_phase_token,
        score_cea_point,
        score_ellingham_point,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    written = 0
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{path.name}-",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            for observation in context.observations.values():
                origin = context.origins.get(observation.observation_id)
                source_id = observation.source_id or ""
                if not (
                    is_compilation_evidence(observation)
                    or is_compilation_source(source_id, origin)
                ):
                    continue
                if not (
                    source_id in {"janaf", "janaf-4th", "nist-janaf-4th"}
                    or (origin or "").replace("\\", "/").startswith(
                        "compilations-janaf/"
                    )
                ):
                    continue
                if is_internal_consistency(origin) or is_sf04_workbook(observation):
                    continue

                for point in _iter_compilation_series_points(observation, origin):
                    identity = point.identity
                    if not isinstance(identity, Identity):
                        continue
                    quantity = quantity_token(identity)
                    if quantity is not Quantity.DELTA_FG:
                        continue
                    temperature = identity.temperature_K
                    if (
                        not isinstance(temperature, State)
                        or not temperature.is_value
                        or temperature.value is None
                        or point.value.kind is not ValueKind.POINT
                        or point.value.point is None
                    ):
                        continue
                    phase = phase_token(identity.species)
                    phase_text = phase.value if isinstance(phase, Phase) else None
                    if phase_text is None:
                        continue
                    phase_kind = classify_phase_token(phase_text)
                    if phase_kind in {"prose", "mixed"}:
                        continue
                    record_id = (
                        None
                        if point.locator is None
                        else point.locator.record
                    )
                    if not record_id:
                        _source, separator, record_id = point.observation_id.partition("::")
                        if not separator:
                            continue
                    temperature_K = float(as_decimal(temperature.value))
                    comparison_point = KeyedTablePoint(
                        compilation_id=COMPILATION_JANAF,
                        record_id=record_id,
                        formula=identity.species.formula,
                        phase=phase_text,
                        phase_kind=phase_kind,
                        T_K=temperature_K,
                        delta_fG_kJ_mol=float(as_decimal(point.value.point)),
                        log10_Kf=None,
                        log10_Kf_as_published=None,
                        printed_page=None,
                        note="JANAF compilation point",
                    )
                    scores = (
                        score_cea_point(comparison_point),
                        score_ellingham_point(comparison_point),
                    )
                    for score in scores:
                        if score is None:
                            continue
                        payload = {
                            "reference_id": point.observation_id,
                            "quantity": quantity.value,
                            "comparison_channel": score.engine_channel,
                            "comparison_key": _compilation_comparison_key(
                                COMPILATION_JANAF,
                                record_id,
                                temperature_K,
                                score.engine_channel,
                                score.comparison_quantity,
                            ),
                            "comparison_quantity": score.comparison_quantity,
                            "status": score.status,
                            "value": (
                                None
                                if score.residual_kJ_mol is None
                                else str(score.residual_kJ_mol)
                            ),
                            "band": str(score.band_kJ_mol),
                        }
                        stream.write(
                            json.dumps(
                                payload,
                                sort_keys=True,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                            + "\n"
                        )
                        written += 1
            stream.flush()
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return written


def iter_compilation_comparisons_jsonl(path: Path) -> Iterable[dict[str, object]]:
    """Read a compilation comparison sidecar one record at a time."""

    if not path.is_file():
        return
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                yield payload


def _prediction_from_attempt(engine: Engine, observation: Observation, attempt: ThermoAttempt):
    from simulator.battery.score import (
        ENGINE_CHANNELS,
        ENGINE_COEFFICIENT_SOURCES,
        EnginePrediction,
        Execution,
        ExecutionState,
    )

    if attempt.value is not None:
        state = ExecutionState.PRODUCED
    elif attempt.refusal_reason is RefusalReason.ATTEMPTED_UNAVAILABLE:
        state = ExecutionState.ATTEMPTED_UNAVAILABLE
    elif attempt.refusal_reason is RefusalReason.UNSUPPORTED:
        state = ExecutionState.UNSUPPORTED
    else:
        state = ExecutionState.NOT_PROBED
    identity = observation.identity if isinstance(observation.identity, Identity) else None
    return EnginePrediction(
        engine=engine,
        channel=ENGINE_CHANNELS[engine],
        execution=Execution(state=state, call_evidence=attempt.call_evidence),
        value=attempt.value,
        unit=attempt.unit,
        authority=attempt.authority,
        notices=attempt.notices,
        coefficient_sources=ENGINE_COEFFICIENT_SOURCES[engine],
        lineage_complete=False,
        refusal_reason=attempt.refusal_reason,
        refusal_detail=attempt.refusal_detail,
        identity=identity,
    )


def _refusal_key(reason: RefusalReason, detail: Mapping[str, object]) -> str:
    token = detail.get("reason")
    if isinstance(token, str) and token:
        return f"{reason.value}:{token}"
    return reason.value


def compilation_tier_census(
    context,
    *,
    engines: Sequence[Engine] | None = None,
    invoke_pure_phase: bool = False,
    audit_compile_residual: bool = True,
) -> dict[str, object]:
    """Per compilation, per quantity, aggregated from scorer decisions.

    ``invoke_pure_phase`` controls whether the scorer attempts MAGEMin or
    ThermoEngine calls. ``audit_compile_residual`` checks a sample of returned
    band/status decisions without rebuilding residuals.
    """

    from simulator.battery.score import (
        ENGINE_CHANNELS,
        ENGINE_COEFFICIENT_SOURCES,
        SCORE_ENGINE_SET,
        EnginePrediction,
        Execution,
        _score_store_with_decisions,
        _validated_score_engines,
        _is_flagged_stratum_notice,
        _is_fusion_conversion_notice,
        comparison_candidates,
        is_compilation_source,
        is_internal_consistency,
        is_sf04_workbook,
        predict_with_engine,
        rail_for_quantity,
    )
    from simulator.battery.enums import ExecutionState

    engine_set = (
        _validated_score_engines(tuple(engines))
        if engines is not None
        else SCORE_ENGINE_SET
    )
    buckets: dict[tuple[str, str], dict[str, object]] = {}
    measured: dict[str, int] = defaultdict(int)
    for obs in comparison_candidates(context):
        quantity = quantity_token(obs.identity) if isinstance(obs.identity, Identity) else None
        formula = obs.identity.species.formula if isinstance(obs.identity, Identity) else ""
        rail = rail_for_quantity(quantity, species_formula=formula)
        measured["none" if rail is None else rail.value] += 1

    series_cells = 0
    banded_series_cells = 0
    transition_series_cells = 0
    reachable = 0
    walked = 0

    def bucket(family: str, quantity: str) -> dict[str, object]:
        key = (family, quantity)
        found = buckets.get(key)
        if found is None:
            found = {
                "reachable": 0,
                "engine_values": 0,
                "numeric": 0,
                "same_source": 0,
                "independent": 0,
                "unknown": 0,
                "match_same_source": 0,
                "match_independent": 0,
                "no_band": 0,
                "residuals": [],
                "decision_strata": [],
                "refused": defaultdict(int),
            }
            buckets[key] = found
        return found

    reused_attempts: dict[tuple[str, Engine], ThermoAttempt] = {}

    def census_predict(engine, observation, **kwargs):
        origin = context.origins.get(observation.observation_id)
        if not (
            is_compilation_evidence(observation)
            or is_compilation_source(observation.source_id, origin)
        ):
            return EnginePrediction(
                engine=engine,
                channel=ENGINE_CHANNELS[engine],
                execution=Execution(state=ExecutionState.NOT_PROBED),
                coefficient_sources=ENGINE_COEFFICIENT_SOURCES[engine],
                lineage_complete=False,
                refusal_reason=RefusalReason.UNSUPPORTED,
                refusal_detail={"reason": "census-non-compilation"},
                identity=observation.identity if isinstance(observation.identity, Identity) else None,
            )
        temperature = (
            observation.identity.temperature_K
            if isinstance(observation.identity, Identity)
            else None
        )
        nonpositive = (
            isinstance(temperature, State)
            and temperature.is_value
            and temperature.value is not None
            and as_decimal(temperature.value) <= 0
        )
        quantity = (
            quantity_token(observation.identity)
            if isinstance(observation.identity, Identity)
            else None
        )
        if not nonpositive and quantity in _THERMO_QUANTITIES and isinstance(
            observation.identity, Identity
        ):
            identity_outcome = identity_equal(
                observation.identity, observation.identity
            )
            if identity_outcome.kind is not IdentityEqualKind.EQUAL:
                kind = identity_outcome.kind
                reason = (
                    RefusalReason.IDENTITY_MISMATCH
                    if kind is IdentityEqualKind.IDENTITY_MISMATCH
                    else RefusalReason.INVALID_IDENTITY
                    if kind is IdentityEqualKind.INVALID_IDENTITY
                    else RefusalReason.IDENTITY_UNKNOWN
                )
                attempt = _refuse(
                    reason,
                    "identity_equal",
                    quantity=quantity,
                    origin=observation.observation_id,
                    extra={
                        "fields": list(identity_outcome.fields),
                        "detail": identity_outcome.detail,
                    },
                )
                return _prediction_from_attempt(engine, observation, attempt)
        if engine is Engine.INTERNAL_ANALYTICAL:
            return predict_with_engine(engine, observation, **kwargs)
        cache_key = (parent_observation_id(observation.observation_id), engine)
        vaporock_gas_table = (
            engine is Engine.VAPOROCK
            and isinstance(observation.identity, Identity)
            and isinstance(observation.identity.species.phase, State)
            and observation.identity.species.phase.is_value
            and observation.identity.species.phase.value is Phase.G
        )
        reuse = (
            engine is not Engine.INTERNAL_ANALYTICAL
            and not invoke_pure_phase
            and not nonpositive
            and not vaporock_gas_table
        )
        attempt = reused_attempts.get(cache_key) if reuse else None
        if attempt is None:
            attempt = predict_thermo_attempt(
                engine,
                observation,
                invoke_pure_phase=invoke_pure_phase,
            )
            if reuse:
                reused_attempts[cache_key] = attempt
        return _prediction_from_attempt(engine, observation, attempt)

    scored_residuals, _candidates, decision_strata = _score_store_with_decisions(
        context,
        engines=engine_set,
        include_diagnostics=True,
        predict=census_predict,
    )

    for obs in context.observations.values():
        origin = context.origins.get(obs.observation_id)
        if is_internal_consistency(origin) or is_sf04_workbook(obs):
            continue
        if not (
            is_compilation_evidence(obs)
            or is_compilation_source(obs.source_id, origin)
        ):
            continue
        quantity = quantity_token(obs.identity) if isinstance(obs.identity, Identity) else None
        if (
            obs.value.kind is ValueKind.SERIES
            and obs.value.series
            and quantity is Quantity.TRANSITION_TEMPERATURE
        ):
            transition_series_cells += len(obs.value.series)
            continue
        expanded = _compilation_series_pairs(obs, origin) is not None
        if expanded:
            point_count = _compilation_series_point_count(obs, origin)
            series_cells += point_count
            if quantity in _THERMO_QUANTITIES:
                banded_series_cells += point_count
        family = compilation_family(obs.source_id, origin)
        walked += 1
        if walked % 2000 == 0:
            print(
                f"compilation census observations={walked} points={reachable}",
                flush=True,
            )
        for point in _iter_compilation_series_points(obs, origin):
            if point.value.kind is not ValueKind.POINT or point.value.point is None:
                continue
            token = quantity_token(point.identity) if isinstance(point.identity, Identity) else None
            row = bucket(family, token.value if token is not None else "unknown")
            row["reachable"] += 1
            reachable += 1

    for residual in scored_residuals:
        observation = reference_observation(context.observations, residual.reference)
        if observation is None:
            continue
        origin = compilation_origin(residual.reference, context.origins)
        if not (
            is_compilation_evidence(observation)
            or is_compilation_source(observation.source_id, origin)
        ):
            continue
        quantity = quantity_token(observation.identity) if isinstance(observation.identity, Identity) else None
        if quantity is None:
            continue
        family = compilation_family(observation.source_id, origin)
        row = bucket(family, quantity.value)
        if residual.execution.state is ExecutionState.PRODUCED:
            row["engine_values"] += 1
        if residual.numeric is None or residual.status is ResidualStatus.REFUSED:
            if residual.refusal is not None:
                key = _refusal_key(residual.refusal.reason, residual.refusal.detail)
                row["refused"][key] += 1
            continue
        row["numeric"] += 1
        if not any(
            _is_flagged_stratum_notice(notice)
            or _is_fusion_conversion_notice(notice)
            for notice in residual.notices
        ):
            row["residuals"].append(residual.numeric.value)
        # An unbanded decision is the more specific bucket and owns same-source rows.
        if residual.status is ResidualStatus.NO_BAND:
            row["no_band"] += 1
        elif residual.source_relation in {SourceRelation.SAME_INPUT, SourceRelation.TRAINING}:
            row["same_source"] += 1
        elif residual.source_relation is SourceRelation.INDEPENDENT:
            row["independent"] += 1
        else:
            row["unknown"] += 1
        if residual.status is ResidualStatus.MATCH:
            if residual.source_relation in {SourceRelation.SAME_INPUT, SourceRelation.TRAINING}:
                row["match_same_source"] += 1
            elif residual.source_relation is SourceRelation.INDEPENDENT:
                row["match_independent"] += 1

    if audit_compile_residual:
        audited = 0
        for residual in scored_residuals:
            observation = reference_observation(context.observations, residual.reference)
            origin = compilation_origin(residual.reference, context.origins)
            if observation is None or not (
                is_compilation_evidence(observation)
                or is_compilation_source(observation.source_id, origin)
            ):
                continue
            if residual.numeric is None or residual.numeric.decision_band is None:
                continue
            if residual.status not in {ResidualStatus.MATCH, ResidualStatus.MISMATCH}:
                continue
            expected = abs(residual.numeric.value) <= residual.numeric.decision_band.value
            if expected != (residual.status is ResidualStatus.MATCH):
                raise RuntimeError(
                    f"scored compilation status/band mismatch {residual.reference}"
                )
            audited += 1
            if audited == 3:
                break

    for stratum in decision_strata:
        row = bucket(str(stratum["family"]), str(stratum["quantity"]))
        row["decision_strata"].append(stratum)
    rendered = []
    for (family, quantity), row in sorted(buckets.items()):
        rendered.append(
            {
                "family": family,
                "quantity": quantity,
                "reachable": row["reachable"],
                "engine_values": row["engine_values"],
                "numeric": row["numeric"],
                "same_source": row["same_source"],
                "independent": row["independent"],
                "unknown": row["unknown"],
                "match_same_source": row["match_same_source"],
                "match_independent": row["match_independent"],
                "no_band": row["no_band"],
                "median_abs_residual": _median_abs(row["residuals"]),
                "decision_strata": row["decision_strata"],
                "refused": dict(sorted(row["refused"].items())),
            }
        )
    return {
        "measured_candidates_by_rail": dict(sorted(measured.items())),
        "comparison_candidates": sum(measured.values()),
        "reachable_points": reachable,
        "series_cells_expanded": series_cells,
        "banded_series_cells_expanded": banded_series_cells,
        "transition_temperature_series_cells_left": transition_series_cells,
        "rows": rendered,
    }
