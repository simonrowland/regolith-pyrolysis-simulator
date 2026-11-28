"""Resolve a species' declared wall reactions to the operative saturation pressure.

The pressure algebra lives in :func:`simulator.vapour_rail.nasa_cea.wall_saturation_pressure_pa`.
This module does the work that function refuses: it reads the species'
``wall_reactions`` declaration, checks atom balance, chooses the condensed
record whose published interval contains the wall temperature, and hands the
resolved Gibbs terms to that function. It does not contain a second K.

Phase selection
---------------
Premise. At fixed T and the table standard pressure, the stable pure
condensed phase among records whose published interval contains T is the
one with the lowest Gibbs energy, activity 1. A NASA record whose phase is
``L`` is liquid. A JANAF ``state`` of ``l`` is liquid. Gas records are the
vapour or O2, never a product. Every other cited phase is a solid. A
published interval with ``T_min >= T_max`` is degenerate: ``Nasa9Segment``
rejects it, and it is not a temperature record. Skipping it leaves the
covering interval's G unchanged.

Algebra. If any cited interval covers T, choose the minimum G°/(R T). At
fixed T that ratio is dimensionless and strictly increasing in G°, so the
minimum is the minimum of G°. An exact tie keeps the record declared first.
A higher-G solid that still covers T is metastable and is not the standard
state. If none covers T, choose the record whose nearest published interval
edge has the smallest distance to T, d(T) = min(|T − T_min|,
|T − T_max|); among equal distances, choose the minimum extrapolated G°/(R T),
then keep declaration order for an exact tie. d is in kelvin and G°/(R T) is
dimensionless. At a shared interval edge d = 0 and the covering-record rule
applies. The continued record remains flagged extrapolated (d-019).

Units. The nearest-edge distance is in kelvin; G°/(R T) is dimensionless.
T is kelvin. No conversion is required to compare G°/(R T). The tables are
the 0.1 MPa / 1 bar standard state the equilibrium constant uses. Condensed
PΔV between phases is not applied.

Sanity. Silicon liquid (NASA NG-1859, JANAF Si-003) has lower G than crystal
(NG-1858, Si-002) above the melting point. JANAF marks crystal ↔ liquid at
1685 K and the crystal table keeps going, so the crystal still covers
1773.15 K and must lose. NASA's crystal interval ends at 1690 K, so above
that only the liquid covers. Below the melting point the crystal is lower,
or it is the only covering phase. At 298.15 K neither cited NASA SiO2 record
covers; NG-1862 begins 1.85 K away at 300 K while liquid NG-1865 begins at
1996 K, so the nearest-interval prediction is NG-1862 and remains flagged.

Missing or invalid records still refuse; no failure is converted to Psat = 0.

A declaration with ``missing: wall_condensation_thermo_missing`` refuses with
that reason. NiO, CoO and MnO are that case: the corpus has no condensed row
to evaluate. With missing parcel pO2, the law uses a flagged pO2-free route when
available; otherwise it refuses wall_pO2_required.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from simulator.reference_data import janaf
from simulator.reference_data.janaf import TABLES_DIR
from simulator.reference_data.nasa_glenn import (
    COMPILATION_ROOT,
    accepted_formula,
    load_record_document,
)
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
    in the JANAF 0.1 MPa standard state. The checked JANAF owner refuses
    missing interior nodes. Outside the printed span the wall path explicitly
    requests that owner's endpoint continuation and marks it extrapolated.
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
    """Select the lowest-G covering phase or the nearest extrapolated record.

    Premise, algebra, units, and the silicon sanity check are in the module
    docstring. Covering records keep the minimum-G°/(R T) rule. If none
    covers, nearest interval wins, then minimum G°/(R T), then declaration
    order. Extrapolated candidates remain flagged by the caller.
    """

    record_ids = _record_ids(thermo, citation, what=what)
    covering: list[tuple[float, int, str, str, str, tuple[float, float]]] = []
    extrapolated: list[tuple[float, int, str, str, str, tuple[float, float]]] = []
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
        bucket = extrapolated if authority == "extrapolated" else covering
        bucket.append((g_over_rt, index, record_id, phase, authority, valid_range))
    # Covering phases are comparable as standard states. Outside all cited
    # intervals, prefer the nearest source domain before comparing continuations.
    pool = covering or extrapolated
    if not pool:
        raise WallSaturationLawRefusal(
            "wall_condensation_thermo_missing",
            f"{what} has no usable condensed record",
        )
    if covering:
        selected = min(pool, key=lambda item: (item[0], item[1]))
    else:
        selected = min(
            pool,
            key=lambda item: (
                min(abs(T_K - item[5][0]), abs(T_K - item[5][1])),
                item[0],
                item[1],
            ),
        )
    g_over_rt, _index, record_id, phase, authority, valid_range = selected
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


def _source_file_version(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino


@lru_cache(maxsize=256)
def _nasa_record_data(
    path: Path,
    _version: tuple[int, int, int, int],
) -> tuple[str, tuple[Nasa9Segment, ...], NasaCeaPolynomial | None]:
    """Prepared coefficients keyed by the source path and its file version."""

    document = load_record_document(path)
    _source_formula, diagnostic = accepted_formula(document)
    if _source_formula is None or (
        diagnostic is not None and diagnostic.get("identity_refusal") is True
    ):
        raise WallSaturationLawRefusal(
            "wall_nasa_source_identity_mismatch",
            f"{path.stem} has no usable NASA Glenn chemical identity: {diagnostic}",
        )
    phase = str(document.get("phase") or "")
    intervals = document.get("intervals")
    if not isinstance(intervals, Sequence) or isinstance(intervals, (str, bytes)):
        raise WallSaturationLawRefusal(
            "wall_condensation_thermo_missing",
            f"{path.stem} has no intervals",
        )
    segments: list[Nasa9Segment] = []
    for interval in intervals:
        if not isinstance(interval, Mapping):
            continue
        t_min = _published_float(interval.get("T_min_K"), f"{path.stem} T_min_K")
        t_max = _published_float(interval.get("T_max_K"), f"{path.stem} T_max_K")
        # Collapsed published intervals are not records. Nasa9Segment rejects
        # T_min >= T_max. The interval that remains still supplies G.
        if t_min >= t_max:
            continue
        coefficients = interval.get("a_coefficients")
        if not isinstance(coefficients, Sequence) or len(coefficients) != 7:
            raise WallSaturationLawRefusal(
                "wall_condensation_thermo_missing",
                f"{path.stem} interval is missing 7 a-coefficients",
            )
        segments.append(
            Nasa9Segment(
                t_min,
                t_max,
                tuple(
                    _published_float(coefficient, f"{path.stem} a")
                    for coefficient in coefficients
                ),
                _published_float(interval.get("b1"), f"{path.stem} b1"),
                _published_float(interval.get("b2"), f"{path.stem} b2"),
            )
        )
    if not segments:
        raise WallSaturationLawRefusal(
            "wall_condensation_thermo_missing",
            f"{path.stem} has no non-degenerate interval",
        )
    formula = document.get("formula")
    formula_s = str(formula) if formula is not None else None
    segments_tuple = tuple(segments)
    try:
        polynomial = NasaCeaPolynomial(
            name=path.stem,
            family="nasa_cea_9",
            standard_state=_nasa_standard_state(phase),
            segments=segments_tuple,
            formula=formula_s,
        )
    except NasaCeaSegmentError:
        polynomial = None
    return phase, segments_tuple, polynomial


@lru_cache(maxsize=256)
def _janaf_record_data(
    path: Path,
    _version: tuple[int, int, int, int],
) -> tuple[str, tuple[tuple[Decimal, Decimal], ...], tuple[Decimal, ...]]:
    """Checked owner rows keyed by the source path and its file version."""

    try:
        table, _digest = janaf._checked_compiled_table_document(
            path.stem,
            document_loader=lambda _path: janaf.load_table_document(path),
        )
        points, missing = janaf._formation_gibbs_rows_from_table(path.stem, table)
    except (ValueError, OSError) as exc:
        raise WallSaturationLawRefusal(
            "wall_condensation_thermo_missing", f"{path.stem}: {exc}",
        ) from exc
    index = table.get("index_entry")
    state = str(index.get("state") or "") if isinstance(index, Mapping) else ""
    return _janaf_phase(state, path.stem), points, missing


def _nasa_record_state(
    record_id: str, T_K: float,
) -> tuple[float, str, str, tuple[float, float]]:
    path = COMPILATION_ROOT / "records" / f"{record_id}.json"
    if not path.is_file():
        raise WallSaturationLawRefusal(
            "wall_condensation_thermo_missing",
            f"NASA record {record_id} is not in the corpus",
        )
    return _nasa_state_for_source(path, _source_file_version(path), T_K)


# Many wall reactions share a Gibbs participant at the same T_wall. Keying
# that evaluated state by source version and temperature avoids repeating the
# C2A hot-face lookup while retaining live values when either input changes.
@lru_cache(maxsize=8192)
def _nasa_state_for_source(
    path: Path,
    version: tuple[int, int, int, int],
    T_K: float,
) -> tuple[float, str, str, tuple[float, float]]:
    phase, segments, polynomial = _nasa_record_data(path, version)
    segment = None
    if polynomial is not None:
        try:
            # The cached polynomial already validates interval coverage. Its
            # segment lookup keeps the shared-endpoint rule without allocating
            # a ThermoState for every hot-wall query.
            segment = polynomial.segment_for(T_K)
        except NasaCeaDomainError:
            pass
    else:
        covering = [
            item
            for item in segments
            if item.T_min_K <= T_K <= item.T_max_K
        ]
        if covering:
            # A gap elsewhere must not drop the interval that does contain T.
            # The shared-endpoint rule assigns T to the higher segment.
            covering.sort(key=lambda item: item.T_min_K)
            segment = covering[-1]
    if segment is not None:
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
    return _janaf_state_for_source(path, _source_file_version(path), T_K)


@lru_cache(maxsize=8192)
def _janaf_state_for_source(
    path: Path,
    version: tuple[int, int, int, int],
    T_K: float,
) -> tuple[float, str, str, tuple[float, float]]:
    phase, points, missing = _janaf_record_data(path, version)
    valid_range = (float(points[0][0]), float(points[-1][0]))
    temperature = Decimal(str(T_K))
    extrapolated = temperature < points[0][0] or temperature > points[-1][0]
    try:
        g_kJ = janaf._interpolate_formation_gibbs(
            points, temperature, missing, extrapolate=extrapolated,
        )
    except ValueError as exc:
        raise WallSaturationLawRefusal(
            "wall_condensation_thermo_missing", f"{path.stem}: {exc}",
        ) from exc
    # kJ/mol → dimensionless G/(RT). 1000 J/kJ. R is J/(mol·K). T is K.
    g_over_rt = (float(g_kJ) * 1000.0) / (R_J_PER_MOL_K * T_K)
    return g_over_rt, phase, "extrapolated" if extrapolated else "authoritative", valid_range


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
