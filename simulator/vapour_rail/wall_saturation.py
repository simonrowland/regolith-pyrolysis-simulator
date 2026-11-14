"""Resolve a species' declared wall reactions to the operative saturation pressure.

The pressure algebra lives in :func:`simulator.vapour_rail.nasa_cea.wall_saturation_pressure_pa`.
This module does the work that function refuses: it reads the species'
``wall_reactions`` declaration, checks atom balance, chooses the condensed
record whose published interval contains the wall temperature, and hands the
resolved Gibbs terms to that function. It does not contain a second K.

Phase selection
---------------
Premise. The wall standard state is the condensed phase stable at ``T_wall``,
activity 1. A NASA record whose phase is ``L`` is liquid. A JANAF ``state``
of ``l`` is liquid. Gas records are the vapour or O2, never a product.
Every other cited phase is a solid. A published interval with
``T_min >= T_max`` is degenerate: ``Nasa9Segment`` rejects it, and it is not
a temperature record. Skipping it leaves the covering interval's G unchanged.

Rule. Among non-liquid records whose remaining interval contains ``T``, the
lowest G wins. The liquid is used only when no solid contains ``T``. An exact
G tie keeps the record declared first. Outside cited intervals, the nearest
NASA segment or JANAF pair is continued and the resulting saturation is
marked extrapolated (d-019). Missing or invalid records still refuse; no
failure is converted to Psat = 0.

A declaration with ``missing: wall_condensation_thermo_missing`` refuses with
that reason. NiO, CoO and MnO are that case: the corpus has no condensed row
to evaluate. With missing parcel pO2, the law uses a flagged pO2-free route when
available; otherwise it refuses wall_pO2_required.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from simulator.reference_data.janaf import TABLES_DIR, load_table_document
from simulator.reference_data.nasa_glenn import COMPILATION_ROOT, load_record_document
from simulator.vapour_rail.nasa_cea import (
    R_J_PER_MOL_K,
    Nasa9Segment,
    NasaCeaDomainError,
    NasaCeaPolynomial,
    NasaCeaSegmentError,
    WallCondensedTerm,
    WallSaturationLawRefusal,
    WallSaturationLawResult,
    WallSaturationReaction,
    operative_wall_saturation,
    wall_saturation_ladder,
)

_BALANCE_TOLERANCE = 1.0e-9
_LIQUID_PHASES = frozenset({"L", "l"})
_GAS_PHASES = frozenset({"gas", "g", "ref"})


@dataclass(frozen=True)
class ResolvedParticipant:
    """One participant of the winning reaction, with the record that supplied G."""

    role: str
    species: str
    record_id: str
    g_over_RT: float
    authority_level: str = "authoritative"
    valid_range_K: tuple[float, float] | None = None


@dataclass(frozen=True)
class ResolvedWallSaturation:
    """Operative wall equilibrium for one declared species at one temperature."""

    species_id: str
    pressure_Pa: float
    reaction_id: str
    nu_vapour: float
    nu_O2: float
    o2_mol_per_mol_vapour: float
    products_mol_per_mol_vapour: tuple[tuple[str, float], ...]
    phase_note: str
    thermo: str
    participants: tuple[ResolvedParticipant, ...]
    notices: tuple[str, ...] = ()


def wall_reactions_declaration(species_row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The species' ``wall_reactions`` block, or None when it has none.

    Absence is not a pressure. Callers that require a wall law treat None as
    "this species is not on the wall-reaction path."
    """

    block = species_row.get("wall_reactions")
    if block is None:
        return None
    if not isinstance(block, Mapping):
        raise WallSaturationLawRefusal(
            "wall_reaction_declaration_invalid",
            "wall_reactions must be a mapping",
        )
    return block


def nasa_record_g_over_rt(record_id: str, T_K: float) -> float:
    """G°/(R T) of one NASA Glenn record at ``T_K``.

    Degenerate intervals (``T_min >= T_max``) are dropped before the
    polynomial is built. The Gibbs ratio is the covering segment's value;
    dropping a collapsed interval does not change it. Outside every remaining
    interval the nearest NASA segment is continued and marked extrapolated.
    """

    g_over_rt, _phase, _authority, _valid_range = _nasa_record_state(record_id, T_K)
    return g_over_rt


def janaf_record_g_over_rt(table_id: str, T_K: float) -> float:
    """JANAF ΔfG/(R T) by linear interpolation of the published rows.

    ``formation_gibbs_energy`` is kJ/mol. ``G/(R T) = (kJ/mol · 1000) / (R T)``
    with ``R`` in J/(mol·K) and ``T`` in kelvin, so the ratio is dimensionless
    in the JANAF 0.1 MPa standard state. The bracket is the nearest published
    rows that carry a value. Outside that span the nearest pair is linearly
    continued and marked extrapolated. This is not
    ``janaf._interpolate_formation_gibbs``, which is tied to the fusion tables.
    """

    g_over_rt, _phase, _authority, _valid_range = _janaf_record_state(table_id, T_K)
    return g_over_rt


def admissible_wall_reactions(
    species_row: Mapping[str, Any],
    *,
    species_id: str,
    T_K: float,
) -> tuple[WallSaturationReaction, ...]:
    """Balanced reactions whose participants have resolvable records at ``T_K``.

    Out-of-range participant records are continued from the nearest source
    segment and their status is carried by the resolved operative result.
    Does not read pO2. The operative pressure is :func:`resolve_wall_saturation`.
    """

    prepared = _prepare_reactions(species_row, species_id=species_id, T_K=T_K)
    return tuple(item.reaction for item in prepared)


def ranked_wall_saturations(
    species_row: Mapping[str, Any],
    *,
    species_id: str,
    T_K: float,
    pO2_Pa: float | None = None,
) -> tuple[WallSaturationLawResult, ...]:
    """Every admissible reaction, lowest saturation pressure first.

    The ledger walks this ladder. The first entry is the operative
    reaction. A later entry is booked only when oxidant runs out and
    that reaction is itself supersaturated. This is not a second K.
    """

    prepared = _prepare_reactions(species_row, species_id=species_id, T_K=T_K)
    return wall_saturation_ladder(
        tuple(item.reaction for item in prepared),
        T_K=T_K,
        pO2_Pa=pO2_Pa,
    )


def resolve_wall_saturation(
    species_row: Mapping[str, Any],
    *,
    species_id: str,
    T_K: float,
    pO2_Pa: float | None = None,
) -> ResolvedWallSaturation:
    """Operative wall Psat for one species row.

    ``Psat_wall = min Psat`` over the admissible reactions. The products and
    the O2 debit or credit are the winning stoichiometry per mole of vapour.
    A species with no ``wall_reactions`` block refuses
    ``wall_reactions_absent`` rather than returning a pressure.
    """

    prepared = _prepare_reactions(species_row, species_id=species_id, T_K=T_K)
    winner = operative_wall_saturation(
        tuple(item.reaction for item in prepared),
        T_K=T_K,
        pO2_Pa=pO2_Pa,
    )
    chosen = next(item for item in prepared if item.reaction.reaction_id == winner.reaction_id)
    block = wall_reactions_declaration(species_row)
    note = ""
    if block is not None and block.get("phase_note") is not None:
        note = str(block.get("phase_note"))
    thermo = "nasa"
    if block is not None and block.get("thermo") is not None:
        thermo = str(block.get("thermo"))
    notices = tuple(dict.fromkeys((*winner.notices, *chosen.notices)))
    return ResolvedWallSaturation(
        species_id=str(species_id),
        pressure_Pa=winner.pressure_Pa,
        reaction_id=winner.reaction_id,
        nu_vapour=winner.nu_vapour,
        nu_O2=winner.nu_O2,
        o2_mol_per_mol_vapour=winner.o2_mol_per_mol_vapour,
        products_mol_per_mol_vapour=winner.products_mol_per_mol_vapour,
        phase_note=note,
        thermo=thermo,
        participants=chosen.participants,
        notices=notices,
    )


@dataclass(frozen=True)
class _PreparedReaction:
    reaction: WallSaturationReaction
    participants: tuple[ResolvedParticipant, ...]
    notices: tuple[str, ...] = ()


def _prepare_reactions(
    species_row: Mapping[str, Any],
    *,
    species_id: str,
    T_K: float,
) -> tuple[_PreparedReaction, ...]:
    block = wall_reactions_declaration(species_row)
    if block is None:
        raise WallSaturationLawRefusal(
            "wall_reactions_absent",
            f"{species_id} has no wall_reactions declaration",
        )
    if block.get("missing") is not None:
        raise WallSaturationLawRefusal(
            str(block.get("missing")),
            str(block.get("note") or f"{species_id} wall thermo is missing"),
        )
    thermo = str(block.get("thermo") or "nasa")
    if thermo not in {"nasa", "janaf"}:
        raise WallSaturationLawRefusal(
            "wall_reaction_declaration_invalid",
            f"{species_id} thermo must be nasa or janaf; got {thermo!r}",
        )
    reactions = block.get("reactions")
    if not isinstance(reactions, Sequence) or isinstance(reactions, (str, bytes)):
        raise WallSaturationLawRefusal(
            "wall_reaction_declaration_invalid",
            f"{species_id} wall_reactions.reactions must be a list",
        )
    if not reactions:
        raise WallSaturationLawRefusal(
            "wall_condensation_thermo_missing",
            f"{species_id} declares no wall reaction",
        )
    for reaction in reactions:
        if not isinstance(reaction, Mapping):
            raise WallSaturationLawRefusal(
                "wall_reaction_declaration_invalid",
                f"{species_id} wall reaction must be a mapping",
            )
        _assert_atom_balance(species_id, reaction)

    oxygen = block.get("oxygen")
    prepared: list[_PreparedReaction] = []
    failures: list[WallSaturationLawRefusal] = []
    for reaction in reactions:
        assert isinstance(reaction, Mapping)
        try:
            prepared.append(
                _build_reaction(
                    species_id,
                    reaction,
                    thermo=thermo,
                    oxygen=oxygen if isinstance(oxygen, Mapping) else None,
                    T_K=T_K,
                )
            )
        except WallSaturationLawRefusal as exc:
            failures.append(exc)
    if prepared:
        return tuple(prepared)
    if not failures:
        raise WallSaturationLawRefusal(
            "wall_condensation_thermo_missing",
            f"{species_id} has no admissible wall reaction",
        )
    reasons = {failure.reason for failure in failures}
    if reasons == {"wall_condensation_thermo_out_of_interval"}:
        raise failures[0]
    missing = [
        failure
        for failure in failures
        if failure.reason == "wall_condensation_thermo_missing"
    ]
    if missing:
        raise missing[0]
    raise failures[0]


def _build_reaction(
    species_id: str,
    reaction: Mapping[str, Any],
    *,
    thermo: str,
    oxygen: Mapping[str, Any] | None,
    T_K: float,
) -> _PreparedReaction:
    reaction_id = _required_text(reaction.get("id"), f"{species_id} reaction id")
    nu_v = _required_number(
        reaction.get("vapour_stoichiometry"),
        f"{species_id}.{reaction_id} vapour_stoichiometry",
    )
    nu_O2 = _required_number(
        reaction.get("oxygen_stoichiometry"),
        f"{species_id}.{reaction_id} oxygen_stoichiometry",
    )
    vapour = reaction.get("vapour")
    if not isinstance(vapour, Mapping):
        raise WallSaturationLawRefusal(
            "wall_reaction_declaration_invalid",
            f"{species_id}.{reaction_id} vapour must be a mapping",
        )
    vapour_formula = _required_text(
        vapour.get("formula"), f"{species_id}.{reaction_id} vapour formula"
    )
    vapour_g, vapour_record, vapour_phase, vapour_authority, vapour_range = _one_record(
        thermo, vapour, T_K, what=f"{species_id}.{reaction_id} vapour"
    )
    if vapour_phase not in _GAS_PHASES:
        raise WallSaturationLawRefusal(
            "wall_reaction_declaration_invalid",
            f"{vapour_record} cited as {species_id} vapour has phase {vapour_phase}",
        )
    participants = [ResolvedParticipant(
        "vapour", vapour_formula, vapour_record, vapour_g,
        vapour_authority, vapour_range,
    )]
    notices: list[str] = []
    if vapour_authority == "extrapolated":
        notices.append("wall_condensation_thermo_extrapolated")
    products_raw = reaction.get("products")
    if not isinstance(products_raw, Sequence) or isinstance(products_raw, (str, bytes)):
        raise WallSaturationLawRefusal(
            "wall_reaction_declaration_invalid",
            f"{species_id}.{reaction_id} products must be a list",
        )
    condensed: list[WallCondensedTerm] = []
    for index, product in enumerate(products_raw):
        if not isinstance(product, Mapping):
            raise WallSaturationLawRefusal(
                "wall_reaction_declaration_invalid",
                f"{species_id}.{reaction_id} product {index} must be a mapping",
            )
        species = _required_text(
            product.get("species"),
            f"{species_id}.{reaction_id} product species",
        )
        stoich = _required_number(
            product.get("stoichiometry"),
            f"{species_id}.{reaction_id}.{species} stoichiometry",
        )
        g_over_rt, record_id, phase, authority, valid_range = _select_condensed(
            thermo,
            product,
            T_K,
            what=f"{species_id}.{reaction_id}.{species}",
        )
        if phase in _GAS_PHASES:
            raise WallSaturationLawRefusal(
                "wall_reaction_declaration_invalid",
                f"{record_id} cited as condensed {species} is a gas",
            )
        condensed.append(WallCondensedTerm(species, stoich, g_over_rt))
        participants.append(ResolvedParticipant(
            "product", species, record_id, g_over_rt, authority, valid_range,
        ))
        if authority == "extrapolated":
            notices.append("wall_condensation_thermo_extrapolated")

    g_oxygen: float | None = None
    if nu_O2 != 0.0:
        if oxygen is None:
            raise WallSaturationLawRefusal(
                "wall_condensation_thermo_missing",
                f"{species_id}.{reaction_id} needs an oxygen record",
            )
        oxygen_g, oxygen_record, oxygen_phase, oxygen_authority, oxygen_range = _one_record(
            thermo, oxygen, T_K, what=f"{species_id}.{reaction_id} oxygen"
        )
        if oxygen_phase not in _GAS_PHASES:
            raise WallSaturationLawRefusal(
                "wall_reaction_declaration_invalid",
                f"{oxygen_record} cited as oxygen has phase {oxygen_phase}",
            )
        g_oxygen = oxygen_g
        participants.append(ResolvedParticipant(
            "oxygen", "O2", oxygen_record, oxygen_g,
            oxygen_authority, oxygen_range,
        ))
        if oxygen_authority == "extrapolated":
            notices.append("wall_condensation_thermo_extrapolated")

    built = WallSaturationReaction(
        reaction_id=reaction_id,
        nu_vapour=nu_v,
        g_vapour=vapour_g,
        condensed=tuple(condensed),
        nu_O2=nu_O2,
        g_oxygen=g_oxygen,
    )
    return _PreparedReaction(
        built, tuple(participants), tuple(dict.fromkeys(notices)),
    )


def _select_condensed(
    thermo: str,
    citation: Mapping[str, Any],
    T_K: float,
    *,
    what: str,
) -> tuple[float, str, str, str, tuple[float, float]]:
    """Lowest-G solid at T, else the liquid if no solid is cited."""

    record_ids = _record_ids(thermo, citation, what=what)
    solids: list[tuple[float, int, str, str, str, tuple[float, float]]] = []
    liquids: list[tuple[float, int, str, str, str, tuple[float, float]]] = []
    extrapolated_solids: list[
        tuple[float, int, str, str, str, tuple[float, float]]
    ] = []
    extrapolated_liquids: list[
        tuple[float, int, str, str, str, tuple[float, float]]
    ] = []
    for index, record_id in enumerate(record_ids):
        try:
            g_over_rt, phase, authority, valid_range = _record_state(
                thermo, record_id, T_K,
            )
        except WallSaturationLawRefusal:
            raise
        if phase in _GAS_PHASES:
            raise WallSaturationLawRefusal(
                "wall_reaction_declaration_invalid",
                f"{what}: {record_id} is a gas and cannot be the condensed product",
            )
        if authority == "extrapolated":
            bucket = extrapolated_liquids if phase in _LIQUID_PHASES else extrapolated_solids
        else:
            bucket = liquids if phase in _LIQUID_PHASES else solids
        bucket.append((g_over_rt, index, record_id, phase, authority, valid_range))
    # Use a cited phase whose interval covers T before extrapolating another
    # candidate phase. Within the selected authority tier solids remain the
    # standard state, with liquid as fallback only when no solid is available.
    pool = solids or liquids or extrapolated_solids or extrapolated_liquids
    if not pool:
        raise WallSaturationLawRefusal(
            "wall_condensation_thermo_missing",
            f"{what} has no usable condensed record",
        )
    g_over_rt, _index, record_id, phase, authority, valid_range = min(
        pool, key=lambda item: (item[0], item[1]),
    )
    return g_over_rt, record_id, phase, authority, valid_range


def _one_record(
    thermo: str,
    citation: Mapping[str, Any],
    T_K: float,
    *,
    what: str,
) -> tuple[float, str, str, str, tuple[float, float]]:
    record_ids = _record_ids(thermo, citation, what=what)
    if len(record_ids) != 1:
        raise WallSaturationLawRefusal(
            "wall_reaction_declaration_invalid",
            f"{what} must cite one record; got {record_ids!r}",
        )
    record_id = record_ids[0]
    g_over_rt, phase, authority, valid_range = _record_state(thermo, record_id, T_K)
    return g_over_rt, record_id, phase, authority, valid_range


def _record_ids(thermo: str, citation: Mapping[str, Any], *, what: str) -> tuple[str, ...]:
    key = "nasa" if thermo == "nasa" else "janaf"
    raw = citation.get(key)
    if raw is None:
        raise WallSaturationLawRefusal(
            "wall_condensation_thermo_missing",
            f"{what} has no {key} record id",
        )
    if isinstance(raw, str):
        values = (raw,)
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        values = tuple(str(item) for item in raw)
    else:
        raise WallSaturationLawRefusal(
            "wall_reaction_declaration_invalid",
            f"{what} {key} record id must be a string or a list",
        )
    if not values or any(not item for item in values):
        raise WallSaturationLawRefusal(
            "wall_condensation_thermo_missing",
            f"{what} {key} record id is empty",
        )
    return values


def _record_state(
    thermo: str, record_id: str, T_K: float,
) -> tuple[float, str, str, tuple[float, float]]:
    if thermo == "nasa":
        return _nasa_record_state(record_id, T_K)
    return _janaf_record_state(record_id, T_K)


def _nasa_record_state(
    record_id: str, T_K: float,
) -> tuple[float, str, str, tuple[float, float]]:
    path = COMPILATION_ROOT / "records" / f"{record_id}.json"
    if not path.is_file():
        raise WallSaturationLawRefusal(
            "wall_condensation_thermo_missing",
            f"NASA record {record_id} is not in the corpus",
        )
    document = load_record_document(path)
    phase = str(document.get("phase") or "")
    intervals = document.get("intervals")
    if not isinstance(intervals, Sequence) or isinstance(intervals, (str, bytes)):
        raise WallSaturationLawRefusal(
            "wall_condensation_thermo_missing",
            f"{record_id} has no intervals",
        )
    segments: list[Nasa9Segment] = []
    for interval in intervals:
        if not isinstance(interval, Mapping):
            continue
        t_min = _published_float(interval.get("T_min_K"), f"{record_id} T_min_K")
        t_max = _published_float(interval.get("T_max_K"), f"{record_id} T_max_K")
        # Collapsed published intervals are not records. Nasa9Segment rejects
        # T_min >= T_max. The interval that remains still supplies G.
        if t_min >= t_max:
            continue
        coefficients = interval.get("a_coefficients")
        if not isinstance(coefficients, Sequence) or len(coefficients) != 7:
            raise WallSaturationLawRefusal(
                "wall_condensation_thermo_missing",
                f"{record_id} interval is missing 7 a-coefficients",
            )
        segments.append(
            Nasa9Segment(
                t_min,
                t_max,
                tuple(
                    _published_float(coefficient, f"{record_id} a")
                    for coefficient in coefficients
                ),
                _published_float(interval.get("b1"), f"{record_id} b1"),
                _published_float(interval.get("b2"), f"{record_id} b2"),
            )
        )
    if not segments:
        raise WallSaturationLawRefusal(
            "wall_condensation_thermo_missing",
            f"{record_id} has no non-degenerate interval",
        )
    formula = document.get("formula")
    formula_s = str(formula) if formula is not None else None
    try:
        polynomial = NasaCeaPolynomial(
            name=record_id,
            family="nasa_cea_9",
            standard_state=_nasa_standard_state(phase),
            segments=tuple(segments),
            formula=formula_s,
        )
    except NasaCeaSegmentError:
        polynomial = None
    covering = [
        segment
        for segment in segments
        if segment.T_min_K <= T_K <= segment.T_max_K
    ]
    if covering:
        # A gap elsewhere must not drop the interval that does contain T.
        # The shared-endpoint rule assigns T to the higher segment.
        covering.sort(key=lambda segment: segment.T_min_K)
        segment = covering[-1]
        if polynomial is not None:
            try:
                g_over_rt = polynomial.evaluate(T_K).g_over_RT
            except NasaCeaDomainError:
                _cp, _h, _s, g_over_rt = segment.evaluate_ratios(T_K)
        else:
            _cp, _h, _s, g_over_rt = segment.evaluate_ratios(T_K)
        return (
            g_over_rt, phase, "authoritative",
            (segment.T_min_K, segment.T_max_K),
        )

    # d-019: outside the published domain, continue the nearest NASA
    # segment and mark it extrapolated. The segment equations define G/RT
    # directly; temperature remains kelvin and the result is dimensionless.
    # At a boundary this tends continuously to the published endpoint value.
    # No claim of source validation is made beyond the selected interval.
    segment = min(
        segments,
        key=lambda item: min(
            abs(T_K - item.T_min_K), abs(T_K - item.T_max_K),
        ),
    )
    _cp, _h, _s, g_over_rt = segment.evaluate_ratios(T_K)
    return (
        g_over_rt, phase, "extrapolated",
        (segment.T_min_K, segment.T_max_K),
    )


def _nasa_standard_state(phase: str) -> str:
    if phase == "gas":
        return "gas"
    if phase == "L":
        return "condensed_liquid"
    return "condensed_solid"


def _janaf_record_state(
    table_id: str, T_K: float,
) -> tuple[float, str, str, tuple[float, float]]:
    path = TABLES_DIR / f"{table_id}.yaml"
    if not path.is_file():
        raise WallSaturationLawRefusal(
            "wall_condensation_thermo_missing",
            f"JANAF table {table_id} is not in the corpus",
        )
    document = load_table_document(path)
    table = document.get("table")
    if not isinstance(table, Mapping):
        raise WallSaturationLawRefusal(
            "wall_condensation_thermo_missing",
            f"{table_id} has no table",
        )
    index = table.get("index_entry")
    state = ""
    if isinstance(index, Mapping) and index.get("state") is not None:
        state = str(index.get("state"))
    phase = _janaf_phase(state, table_id)
    rows = table.get("values")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise WallSaturationLawRefusal(
            "wall_condensation_thermo_missing",
            f"{table_id} has no values",
        )
    points: list[tuple[float, float]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        temperature = row.get("temperature")
        gibbs = row.get("formation_gibbs_energy")
        if not isinstance(temperature, Mapping) or not isinstance(gibbs, Mapping):
            continue
        if temperature.get("value") is None or gibbs.get("value") is None:
            continue
        points.append((float(temperature["value"]), float(gibbs["value"])))
    if len(points) < 2:
        raise WallSaturationLawRefusal(
            "wall_condensation_thermo_missing",
            f"{table_id} has fewer than two formation-Gibbs rows",
        )
    points.sort()
    valid_range = (points[0][0], points[-1][0])
    authority = (
        "extrapolated"
        if T_K < points[0][0] or T_K > points[-1][0]
        else "authoritative"
    )
    g_kJ = _linear_bracket(points, T_K, table_id)
    # kJ/mol → dimensionless G/(RT). 1000 J/kJ. R is J/(mol·K). T is K.
    g_over_rt = (g_kJ * 1000.0) / (R_J_PER_MOL_K * T_K)
    return g_over_rt, phase, authority, valid_range


def _linear_bracket(
    points: Sequence[tuple[float, float]],
    temperature_K: float,
    table_id: str,
) -> float:
    if temperature_K < points[0][0]:
        left, right = points[0], points[1]
    elif temperature_K > points[-1][0]:
        left, right = points[-2], points[-1]
    else:
        bracket = next((
            (left, right)
            for left, right in zip(points, points[1:])
            if left[0] <= temperature_K <= right[0]
        ), None)
        if bracket is None:
            raise WallSaturationLawRefusal(
                "wall_condensation_thermo_out_of_interval",
                f"{table_id} formation Gibbs does not cover {temperature_K} K",
            )
        left, right = bracket
    left_T, left_g = left
    right_T, right_g = right
    if right_T == left_T:
        return left_g
    # Premise: adjacent published ΔfG rows supply the local slope. Algebra
    # continues G(T)=G0+(G1-G0)(T-T0)/(T1-T0), in kJ/mol; the caller converts
    # kJ/mol to dimensionless G/(RT). The temperature ratio is unitless. At
    # T0 the result equals G0; outside the pair it is a flagged prediction.
    return left_g + (right_g - left_g) * (temperature_K - left_T) / (
        right_T - left_T
    )


def _janaf_phase(state: str, table_id: str) -> str:
    if state in _LIQUID_PHASES:
        return "L"
    if state in _GAS_PHASES:
        return "gas"
    if state == "cr":
        return "cr"
    raise WallSaturationLawRefusal(
        "wall_reaction_declaration_invalid",
        f"{table_id} JANAF state {state!r} is not a wall phase",
    )


def _assert_atom_balance(species_id: str, reaction: Mapping[str, Any]) -> None:
    """ν_v V + ν_O2 O2 = Σ ν_s S, with ν_O2 signed (negative means O2 is produced)."""

    from simulator.vapour_rail.catalog import _formula_atoms

    reaction_id = str(reaction.get("id") or species_id)
    vapour = reaction.get("vapour")
    products = reaction.get("products")
    if not isinstance(vapour, Mapping) or not isinstance(products, Sequence):
        raise WallSaturationLawRefusal(
            "wall_reaction_declaration_invalid",
            f"{species_id}.{reaction_id} is missing vapour or products",
        )
    nu_v = _required_number(
        reaction.get("vapour_stoichiometry"),
        f"{species_id}.{reaction_id} vapour_stoichiometry",
    )
    nu_O2 = _required_number(
        reaction.get("oxygen_stoichiometry"),
        f"{species_id}.{reaction_id} oxygen_stoichiometry",
    )
    formula = _required_text(
        vapour.get("formula"), f"{species_id}.{reaction_id} vapour formula"
    )
    balance: dict[str, float] = {}
    for element, count in _formula_atoms(formula).items():
        balance[element] = balance.get(element, 0.0) - nu_v * count
    if nu_O2 != 0.0:
        for element, count in _formula_atoms("O2").items():
            balance[element] = balance.get(element, 0.0) - nu_O2 * count
    if isinstance(products, (str, bytes)) or not products:
        raise WallSaturationLawRefusal(
            "wall_reaction_declaration_invalid",
            f"{species_id}.{reaction_id} has no products",
        )
    for product in products:
        if not isinstance(product, Mapping):
            raise WallSaturationLawRefusal(
                "wall_reaction_declaration_invalid",
                f"{species_id}.{reaction_id} product must be a mapping",
            )
        product_formula = _required_text(
            product.get("formula"),
            f"{species_id}.{reaction_id} product formula",
        )
        stoich = _required_number(
            product.get("stoichiometry"),
            f"{species_id}.{reaction_id} product stoichiometry",
        )
        for element, count in _formula_atoms(product_formula).items():
            balance[element] = balance.get(element, 0.0) + stoich * count
    unbalanced = {
        element: amount
        for element, amount in balance.items()
        if abs(amount) > _BALANCE_TOLERANCE
    }
    if unbalanced:
        raise WallSaturationLawRefusal(
            "wall_reaction_unbalanced",
            f"{species_id}.{reaction_id} is not atom balanced: {unbalanced}",
        )


def _published_float(node: Any, what: str) -> float:
    if isinstance(node, Mapping) and "value" in node:
        node = node.get("value")
    return _required_number(node, what)


def _required_number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WallSaturationLawRefusal(
            "wall_reaction_declaration_invalid",
            f"{what} must be a number; got {value!r}",
        )
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        raise WallSaturationLawRefusal(
            "wall_reaction_declaration_invalid",
            f"{what} must be finite; got {value!r}",
        )
    return number


def _required_text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WallSaturationLawRefusal(
            "wall_reaction_declaration_invalid",
            f"{what} must be a non-empty string",
        )
    return value.strip()


__all__ = [
    "ResolvedParticipant",
    "ResolvedWallSaturation",
    "admissible_wall_reactions",
    "janaf_record_g_over_rt",
    "nasa_record_g_over_rt",
    "resolve_wall_saturation",
    "wall_reactions_declaration",
]
