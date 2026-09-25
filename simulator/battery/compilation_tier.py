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

import os
import re
from collections import defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal
from fractions import Fraction
from typing import Callable, Mapping, Sequence

from simulator.battery.enums import (
    FORMATION_QUANTITIES,
    PURE_STANDARD_THERMO,
    QUANTITY_UNITS,
    Authority,
    Engine,
    EvidenceClass,
    NoticeKind,
    PerBasis,
    Phase,
    Quantity,
    RefusalReason,
    ResidualStatus,
    SourceRelation,
    ValueKind,
)
from simulator.battery.identity import (
    Identity,
    log10K_from_delta_fG_kJ_mol,
    quantity_token,
)
from simulator.battery.records import (
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

_THERMO_QUANTITIES = FORMATION_QUANTITIES | PURE_STANDARD_THERMO
_H298_K = 298.15
_PA_PER_BAR = Decimal("100000")


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

    is_compilation_source, is_internal_consistency, is_sf04_workbook, _parse = (
        _score_predicates()
    )
    if is_internal_consistency(origin) or is_sf04_workbook(observation):
        return (observation,)
    compilation = is_compilation_evidence(observation) or is_compilation_source(
        observation.source_id, origin
    )
    if not compilation:
        return (observation,)
    value = observation.value
    if value.kind is not ValueKind.SERIES or not value.series:
        return (observation,)
    if not isinstance(observation.identity, Identity):
        return (observation,)
    quantity = quantity_token(observation.identity)
    if quantity is Quantity.TRANSITION_TEMPERATURE:
        return (observation,)
    points: list[Observation] = []
    seen: dict[str, int] = {}
    for temperature, magnitude in value.series:
        temp_d = as_decimal(temperature)
        mag_d = as_decimal(magnitude)
        key = f"{_decimal_token(temp_d)}|{_decimal_token(mag_d)}"
        occurrence = seen.get(key, 0)
        seen[key] = occurrence + 1
        identity = replace(
            observation.identity,
            temperature_K=State.of(temp_d),
        )
        points.append(
            replace(
                observation,
                observation_id=compilation_point_id(
                    observation.observation_id, temp_d, mag_d, occurrence
                ),
                identity=identity,
                value=Value.point_of(mag_d),
                derived_from=(observation.observation_id,)
                + tuple(observation.derived_from or ()),
            )
        )
    return tuple(points)


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

    if quantity not in {Quantity.DELTA_FG, Quantity.LOG10_KF}:
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
    if quantity not in {Quantity.CP, Quantity.S, Quantity.H_MINUS_H298}:
        return _refuse(
            RefusalReason.UNSUPPORTED,
            "engine-thermo-does-not-emit",
            quantity=quantity,
            origin=origin,
            extra={"engine": engine.value},
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
    except (ValueError, OSError, RuntimeError) as exc:
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
        return origin.replace("\\", "/").split("/", 1)[0]
    return source_id or "unknown"


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
    engine: str
    status: ResidualStatus
    relation: SourceRelation
    numeric: Decimal | None
    family: str
    quantity: str
    uncertainty: str


def _tier_markdown(cells: Sequence[_TierCell]) -> list[str]:
    by_engine: dict[str, list[_TierCell]] = defaultdict(list)
    by_source: dict[tuple[str, str], list[_TierCell]] = defaultdict(list)
    for cell in cells:
        by_engine[cell.engine].append(cell)
        by_source[(cell.family, cell.quantity)].append(cell)
    lines = [
        "## Compilation tier",
        "",
        "Compilation comparisons: assessed tables and quoted rows stored",
        "under a compilation. Not part of the measured tier and not added",
        "to it. same-source means the engine coefficients resolve to this",
        "compilation (JANAF-4th refit versus JANAF; NASA CEA thermo.inp",
        "versus the Glenn coefficient database). Pending admission is",
        "unchanged. Printed uncertainty is the reference observation's",
        "uncertainty (often none on a grid).",
        "",
        "| engine | comparisons | refused | numeric | same-source | independent | match same-source | match independent |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    if not by_engine:
        lines.append("| (none) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |")
    for engine, bucket in sorted(by_engine.items()):
        numeric = [row for row in bucket if row.numeric is not None]
        same = [
            row
            for row in numeric
            if row.relation in {SourceRelation.SAME_INPUT, SourceRelation.TRAINING}
        ]
        independent = [row for row in numeric if row.relation is SourceRelation.INDEPENDENT]
        lines.append(
            f"| {engine} | {len(bucket)} | "
            f"{sum(1 for row in bucket if row.status is ResidualStatus.REFUSED)} | "
            f"{len(numeric)} | {len(same)} | {len(independent)} | "
            f"{sum(1 for row in same if row.status is ResidualStatus.MATCH)} | "
            f"{sum(1 for row in independent if row.status is ResidualStatus.MATCH)} |"
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
        numeric_values = [row.numeric for row in bucket if row.numeric is not None]
        lines.append(
            f"| `{family}` | `{quantity}` | {len(bucket)} | {len(numeric_values)} | "
            f"{sum(1 for row in bucket if row.status is ResidualStatus.REFUSED)} | "
            f"{_median_abs(numeric_values) or '—'} | {bucket[0].uncertainty} |"
        )
    lines.append("")
    return lines


def _cell_from_observation(
    *,
    engine: str,
    status: ResidualStatus,
    relation: SourceRelation,
    numeric: Decimal | None,
    observation: Observation,
    origin: str | None,
) -> _TierCell:
    quantity = "unknown"
    if isinstance(observation.identity, Identity):
        token = quantity_token(observation.identity)
        quantity = token.value if token is not None else "unknown"
    return _TierCell(
        engine=engine,
        status=status,
        relation=relation,
        numeric=numeric,
        family=compilation_family(observation.source_id, origin),
        quantity=quantity,
        uncertainty=uncertainty_text(observation.uncertainty),
    )


def compilation_tier_lines(
    residuals: Sequence[object],
    observations: Mapping[str, Observation],
    origins: Mapping[str, str] | None = None,
) -> list[str]:
    """Compilation tier beside the measured tier. The two counts are not added."""

    from simulator.battery.score import Residual

    cells: list[_TierCell] = []
    for residual in residuals:
        if not isinstance(residual, Residual):
            continue
        observation = compilation_row_observation(
            residual.reference, observations, origins
        )
        if observation is None:
            continue
        cells.append(
            _cell_from_observation(
                engine=residual.key.rsplit("::", 1)[-1],
                status=residual.status,
                relation=residual.source_relation,
                numeric=None if residual.numeric is None else residual.numeric.value,
                observation=observation,
                origin=compilation_origin(residual.reference, origins),
            )
        )
    return _tier_markdown(cells)


def compilation_tier_lines_from_payloads(
    rows: Sequence[Mapping[str, object]],
    observations: Mapping[str, Observation],
    origins: Mapping[str, str] | None = None,
) -> list[str]:
    """Same compilation table from a residuals.jsonl payload."""

    cells: list[_TierCell] = []
    for row in rows:
        reference = str(row.get("reference") or "")
        observation = compilation_row_observation(reference, observations, origins)
        if observation is None:
            continue
        request = row.get("candidate_request")
        engine = ""
        if isinstance(request, Mapping) and request.get("engine"):
            engine = str(request["engine"])
        if not engine:
            engine = str(row.get("key") or "").rsplit("::", 1)[-1]
        raw_numeric = row.get("numeric")
        numeric = None
        if isinstance(raw_numeric, Mapping) and raw_numeric.get("value") is not None:
            numeric = as_decimal(raw_numeric["value"])
        cells.append(
            _cell_from_observation(
                engine=engine or "unknown",
                status=ResidualStatus(str(row.get("status"))),
                relation=SourceRelation(str(row.get("source_relation") or SourceRelation.UNKNOWN.value)),
                numeric=numeric,
                observation=observation,
                origin=compilation_origin(reference, origins),
            )
        )
    return _tier_markdown(cells)


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
    """Per compilation, per quantity. Does not write the store.

    Ellingham is evaluated at each printed temperature. Other engines'
    thermochemistry refusal does not depend on which printed temperature
    it is, so it is computed once per series when pure-phase is not
    invoked. An invoked pure-phase value depends on T and is not reused.
    ``invoke_pure_phase`` false records ``pure-phase-call-required``
    instead of opening MAGEMin or ThermoEngine. Relation and validity gates are the same functions
    ``compile_residual`` uses. A sample of points is checked against
    ``compile_residual`` when ``audit_compile_residual`` is set.
    """

    from simulator.battery.score import (
        ENGINE_COEFFICIENT_SOURCES,
        SCORE_ENGINE_SET,
        comparison_candidates,
        decision_band_for,
        expand_coefficient_sources,
        is_compilation_source,
        is_internal_consistency,
        is_sf04_workbook,
        lineage_complete_for,
        parse_species_formula,
        rail_for_quantity,
        resolve_source_relation,
    )
    from simulator.battery.validity import run_validity_gates

    engine_set = tuple(engines) if engines is not None else SCORE_ENGINE_SET
    buckets: dict[tuple[str, str], dict[str, object]] = {}
    measured: dict[str, int] = defaultdict(int)
    for obs in comparison_candidates(context):
        quantity = quantity_token(obs.identity) if isinstance(obs.identity, Identity) else None
        formula = obs.identity.species.formula if isinstance(obs.identity, Identity) else ""
        measured[rail_for_quantity(quantity, species_formula=formula).value] += 1

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
                "match_same_source": 0,
                "match_independent": 0,
                "residuals": [],
                "refused": defaultdict(int),
            }
            buckets[key] = found
        return found

    relations: dict[str, dict[Engine, SourceRelation]] = {}
    gate_keys: dict[tuple[str, str], str | None] = {}
    audited = 0

    def relation_for(parent: Observation, engine: Engine) -> SourceRelation:
        found = relations.get(parent.observation_id)
        if found is None:
            found = {}
            relations[parent.observation_id] = found
        if engine not in found:
            sources = ENGINE_COEFFICIENT_SOURCES[engine]
            complete = lineage_complete_for(
                sources,
                works=context.works,
                observations=context.observations,
                experiments=context.experiments,
            )
            found[engine] = resolve_source_relation(
                parent,
                expand_coefficient_sources(sources),
                complete,
                works=context.works,
                observations=context.observations,
                experiments=context.experiments,
            )
        return found[engine]

    def gate_token(parent: Observation, quantity: Quantity | None) -> str | None:
        key = (parent.experiment_id, "" if quantity is None else quantity.value)
        if key not in gate_keys:
            experiment = context.experiments.get(parent.experiment_id)
            if experiment is None:
                gate_keys[key] = RefusalReason.REFERENTIAL_INTEGRITY.value
            else:
                gates = run_validity_gates(experiment, parent)
                gate_keys[key] = None if gates.passed else (
                    gates.reason.value if gates.reason is not None else "validity"
                )
        return gate_keys[key]

    def account(row: dict[str, object], *, attempt: ThermoAttempt | None, relation: SourceRelation, reference: Decimal | None, formula_bad: bool, gate: str | None, quantity: Quantity | None) -> str:
        if formula_bad:
            key = "identity_unknown:species_formula_unparsed"
            row["refused"][key] += 1
            return key
        if gate is not None:
            row["refused"][gate] += 1
            return gate
        if attempt is None or quantity is None or reference is None:
            key = "identity_unknown:quantity_unknown"
            row["refused"][key] += 1
            return key
        if attempt.value is not None:
            row["engine_values"] += 1
            band = decision_band_for(quantity, relation)
            if band is None:
                key = f"decision_rule_missing:no_sourced_decision_band:{quantity.value}"
                row["refused"][key] += 1
                return key
            residual = attempt.value - reference
            row["numeric"] += 1
            row["residuals"].append(residual)
            if relation in {SourceRelation.SAME_INPUT, SourceRelation.TRAINING}:
                row["same_source"] += 1
                if abs(residual) <= band.value:
                    row["match_same_source"] += 1
            elif relation is SourceRelation.INDEPENDENT:
                row["independent"] += 1
                if abs(residual) <= band.value:
                    row["match_independent"] += 1
            return "numeric"
        key = _refusal_key(attempt.refusal_reason or RefusalReason.UNSUPPORTED, attempt.refusal_detail)
        row["refused"][key] += 1
        return key

    from simulator.battery.validate import bound_work_inputs, build_printed_thermo_index

    table_index = build_printed_thermo_index(context.observations)
    with bound_work_inputs(context.works, context.observations, context.experiments):
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
            points = compilation_series_points(obs, origin)
            expanded = bool(points) and points[0].observation_id != obs.observation_id
            if expanded:
                series_cells += len(points)
                if quantity in _THERMO_QUANTITIES:
                    banded_series_cells += len(points)
            family = compilation_family(obs.source_id, origin)
            walked += 1
            if walked % 2000 == 0:
                print(
                    f"compilation census observations={walked} points={reachable}",
                    flush=True,
                )
            formula = ""
            if isinstance(obs.identity, Identity):
                formula = obs.identity.species.formula
            formula_bad = quantity is not None and parse_species_formula(formula) is None
            gate = gate_token(obs, quantity)
            reused: dict[Engine, ThermoAttempt] = {}
            for point in points:
                if point.value.kind is not ValueKind.POINT or point.value.point is None:
                    continue
                token = quantity_token(point.identity) if isinstance(point.identity, Identity) else None
                qname = token.value if token is not None else "unknown"
                row = bucket(family, qname)
                row["reachable"] += 1
                reachable += 1
                reference = point.value.point
                for engine in engine_set:
                    attempt: ThermoAttempt | None
                    if token not in _THERMO_QUANTITIES:
                        attempt = _refuse(
                            RefusalReason.UNSUPPORTED,
                            "not-thermochemistry",
                            quantity=token or Quantity.DELTA_FG,
                            origin=point.observation_id,
                        )
                    else:
                        temperature_state = (
                            point.identity.temperature_K
                            if isinstance(point.identity, Identity)
                            else None
                        )
                        nonpositive = (
                            isinstance(temperature_state, State)
                            and temperature_state.is_value
                            and temperature_state.value is not None
                            and as_decimal(temperature_state.value) <= 0
                        )
                        # A 0 K refusal is not reused. A pure-phase value
                        # depends on T, so an invoked call is not reused either.
                        # The uninvoked refusal does not depend on T.
                        reuse = (
                            not nonpositive
                            and engine is not Engine.INTERNAL_ANALYTICAL
                            and not invoke_pure_phase
                        )
                        if reuse and engine in reused:
                            attempt = reused[engine]
                        else:
                            attempt = predict_thermo_attempt(
                                engine,
                                point,
                                invoke_pure_phase=invoke_pure_phase,
                            )
                            if reuse:
                                reused[engine] = attempt
                    relation = relation_for(obs, engine)
                    account(
                        row,
                        attempt=attempt,
                        relation=relation,
                        reference=reference,
                        formula_bad=formula_bad,
                        gate=gate,
                        quantity=token,
                    )
                    if (
                        audit_compile_residual
                        and audited < 3
                        and engine is Engine.INTERNAL_ANALYTICAL
                        and token in _THERMO_QUANTITIES
                        and not formula_bad
                        and gate is None
                    ):
                        from simulator.battery.score import compile_residual

                        residual, _candidate = compile_residual(
                            point,
                            engine,
                            context=context,
                            comparison_ids=set(),
                            predict=lambda eng, observation, handles=None, isolated=None, _attempt=attempt: _prediction_from_attempt(
                                eng, observation, _attempt
                            ),
                            lineage_observation_id=(
                                obs.observation_id
                                if point.observation_id != obs.observation_id
                                else None
                            ),
                            table_index=table_index,
                        )
                        if residual.numeric is None:
                            got = _refusal_key(
                                residual.refusal.reason, residual.refusal.detail
                            ) if residual.refusal is not None else "none"
                        else:
                            got = f"numeric:{residual.numeric.value}:{residual.source_relation.value}"
                        expect_band = decision_band_for(token, relation)
                        if attempt.value is not None and expect_band is not None:
                            expect = f"numeric:{attempt.value - reference}:{relation.value}"
                        elif attempt.value is not None:
                            expect = f"decision_rule_missing:no_sourced_decision_band:{token.value}"
                        else:
                            expect = _refusal_key(
                                attempt.refusal_reason or RefusalReason.UNSUPPORTED,
                                attempt.refusal_detail,
                            )
                        if got != expect:
                            raise RuntimeError(
                                f"census/compile_residual mismatch {point.observation_id}: {got} != {expect}"
                            )
                        audited += 1
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
                    "match_same_source": row["match_same_source"],
                    "match_independent": row["match_independent"],
                    "median_abs_residual": _median_abs(row["residuals"]),
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
