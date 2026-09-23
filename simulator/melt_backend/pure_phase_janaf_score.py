"""JANAF scoring of engine pure-phase standard-state access (P4a-2).

Owner ruling d-039: JANAF may score MAGEMin and ThermoEngine on
condensed-phase standard-state properties only.  Two row kinds:

* reaction rows -- Delta_rG(T) of a balanced reaction: the engine's
  apparent-G reaction sum vs JANAF's Delta_fG(T) reaction sum;
* phase-property rows -- convention-free S(T), Cp(T) and H(T)-H(298.15)
  of one phase vs the matching JANAF table.

DERIVATION (why an apparent-G reaction sum may be differenced against a
Delta_fG reaction sum): JANAF prints Delta_fG(T), referenced to the
elements in their reference states at T.  Both engines report an APPARENT
Gibbs energy (HP / Berman convention), referenced to the elements at the
298.15 K reference temperature only.  The two conventions differ by
per-element reference offsets that are identical for every phase:

    G_apparent(phase, T) = Delta_fG(phase, T) + sum_e n_e * g_ref_e(T)

For a balanced reaction, sum_i nu_i * n_{i,e} = 0 for every element e, so
the element reference offsets cancel exactly in the reaction sum:

    sum_i nu_i * G_apparent(i, T) = sum_i nu_i * Delta_fG(i, T)

The engine apparent-G reaction sum and the JANAF Delta_fG reaction sum are
therefore the same physical quantity and may be differenced directly.  A
single-phase apparent G is not a Delta_fG until the element term is removed
(``formation_g_from_apparent_kJ_mol``).  Element H and S come from the JANAF
``ref`` tables, which switch crystal, liquid, and gas at the printed markers.

Polymorph contract: a residual is only meaningful inside the temperature
band of one polymorph.  Each JANAF table's bands are read from its own
printed solid-solid markers (``I <--> II`` and so on), not from a single
label stamped on the whole table.  Mg-012 (MgSiO3) prints I->II at 903 K
and II->III at 1258 K; those bands are the printed tokens i / ii / iii.
The table title does not say clinoenstatite, so the whole table is not
labelled clinoenstatite, and an engine clino/ortho/proto row is refused
at every T.  Quartz (O-037) prints the same kind of marker (I->II at
847 K, aliased to alpha/beta by the existing SiO2 transition table) but
the title mineral is quartz and both engine quartz phases already contain
that transition, so alpha and beta score as one mineral and the row names
the printed sub-form.  Cristobalite high and low are two titled tables
(O-035 / O-036) and stay two tokens; the engine's single ``cristobalite``
label matches neither.  A row whose engine polymorph is not the band that
contains T is refused with ``polymorph_mismatch`` and never scored.  This
compilation has no tridymite table.  An unreachable property stays a typed
absence, never a zero; a number returned together with an absence token
is not scored.

``REACTION_CATALOGUE`` is declarative data: the two Mg-silicate reactions
plus spinel and one row per Al2SiO5 polymorph.  CaSiO3, Ca2SiO4 and
stoichiometric FeO are not in this harvest (Fe-001 is Fe0.947O), so those
reactions are absent.  MAGEMin ig has no plain MgAl2O4 endmember (host
``spl``; ``nsp`` is ordered normal spinel), so that engine's spinel row is
a typed refusal rather than a score.

This module is the pure arithmetic/plan half.  The engine/JANAF IO and
report rendering live in ``scripts/janaf_pure_phase_score.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional, Tuple

from simulator.battery.enums import StateTag
from simulator.battery.polymorph_dictionary import (
    JANAF_TRANSITION_POLYMORPHS,
    canonicalize_janaf_transition,
    resolve_janaf_polymorph,
)
from simulator.melt_backend.pure_phase import PurePhaseProperties
from simulator.reference_data.janaf import TABLES_DIR, load_table_document

# Engine polymorph maps are imported from the engines' own modules so this
# consumer never restates phase identity (the maps are the same source the
# accessors use to stamp PurePhaseProperties.polymorph).
from engines.alphamelts.thermoengine import _TE_PURE_PHASE_POLYMORPH
from simulator.melt_backend.magemin import _MAGEMIN_PURE_PHASES

# ----------------------------------------------------------------- tokens --

# Closed refusal-reason tokens (RefusalRow.reason).
POLYMORPH_MISMATCH = 'polymorph_mismatch'
NO_JANAF_TABLE_FOR_POLYMORPH = 'no_janaf_table_for_polymorph'
NO_JANAF_ROW_AT_T = 'no_janaf_row_at_temperature'
# ig host ``spl`` endmember ``nsp`` is ordered normal spinel. JANAF Al-089
# is calorimetric spinel; scoring nsp would fold order-disorder into the
# residual. The row is refused, not mapped.
NO_JUSTIFIED_ENGINE_ENDMEMBER = 'no_justified_engine_endmember'
# One bad engine row must not abort the run (scripts/janaf_pure_phase_score).
ENGINE_PHASE_ACCESS = 'engine_phase_access'

STATUS_SCORED = 'scored'
STATUS_REFUSED = 'refused'

DEFAULT_TEMPERATURES_K = (298.15, 500.0, 1000.0, 1500.0)
PRESSURE_BAR = 1.0

# At 1 bar quartz is the stable SiO2 polymorph only up to the q -> trd
# transition (~1143 K / ~870 C); above it both the engine quartz row and the
# JANAF quartz table row are metastable extensions.  Matched polymorphs, but
# the row is flagged.
QUARTZ_METASTABLE_ABOVE_K = 1143.0

# 1 bar, not JANAF markers. Holdaway 1971 invariant is 501 C / 3.76 kbar.
# The 1-bar kyanite-andalusite intercept on that family is ~220 C
# (Anderson, Newton & Kleppa 1977). Above it kyanite is metastable.
KYANITE_METASTABLE_ABOVE_K = 493.0
# Holdaway 1971 1-bar andalusite-sillimanite intercept, 770 C.
ANDALUSITE_SILLIMANITE_1BAR_K = 1043.0
# Mullite + silica replaces Al2SiO5 at 1 atm above ~1200 C
# (Igami, Ohi & Miyake 2019).
AL2SIO5_MULLITE_ABOVE_K = 1473.0

# ThermoEngine quartz includes the upstream MELTS quartz correction:
# QUARTZ_ADJUSTMENT = -1291.0 J/mol (ThermoEngine src/
# BermanStoichiometricPhases.m:14, added to quartz enthalpy -- and through
# G = H - T*S to Gibbs energy -- whenever isQuartzCorrectionUsed, the
# default).  Every TE reaction with quartz as a term is shifted by
# nu_Qz * (-1.291 kJ); the consumer reports TE residuals with AND without
# the term, labelled.  S and Cp are not adjusted; H(T)-H(298.15) cancels
# the constant, so per-phase rows are unaffected.
MELTS_QUARTZ_ADJUSTMENT_KJ_MOL = -1.291

# JANAF formation-reaction reference state for each element.  The ``ref``
# table already switches crystal -> liquid -> gas at the printed markers
# and keeps H-H(298.15) on the 298.15 K element zero.  The separate cr and
# l tables do not share that zero (Mg boils at 1366.104 K, so the 1500 K
# reference is the gas row of Mg-001, not Mg-003 liquid).
ELEMENT_REFERENCE_TABLES: Mapping[str, str] = {
    'Mg': 'Mg-001',
    'Al': 'Al-001',
    'Si': 'Si-001',
    'O2': 'O-029',
}


class PolymorphMismatchError(ValueError):
    """Engine phase and JANAF table are different polymorphs."""


# ------------------------------------------------------- JANAF table specs --

@dataclass(frozen=True)
class JanafTableSpec:
    table_id: str
    label: str
    # Used only when the printed title names no polymorph AND the table
    # has no solid-solid marker (MgO, Mg2SiO4).  Never a stand-in for a
    # table the markers already split (Mg-012).
    fallback_polymorph: Optional[str] = None


JANAF_TABLES: Mapping[str, JanafTableSpec] = {
    'Mg-008': JanafTableSpec('Mg-008', 'MgO(cr)', 'periclase'),
    'O-037': JanafTableSpec('O-037', 'SiO2(cr, quartz)'),
    'O-036': JanafTableSpec('O-036', 'SiO2(cr, cristobalite-low)'),
    'O-035': JanafTableSpec('O-035', 'SiO2(cr, cristobalite-high)'),
    # Title is "Magnesium Silicate"; polymorphs come from the printed
    # I/II/III markers, not from a clinoenstatite label.
    'Mg-012': JanafTableSpec('Mg-012', 'MgSiO3(cr)'),
    'Mg-028': JanafTableSpec('Mg-028', 'Mg2SiO4(cr)', 'forsterite'),
    # Title names no polymorph. CRYSTAL <--> LIQUID closes the fallback.
    'Al-089': JanafTableSpec('Al-089', 'MgAl2O4(cr)', 'spinel'),
    # Title mineral is corundum; ALPHA <--> LIQUID is the alpha band.
    'Al-096': JanafTableSpec('Al-096', 'Al2O3(cr, alpha)'),
    'Al-102': JanafTableSpec('Al-102', 'Al2SiO5(cr, andalusite)'),
    'Al-103': JanafTableSpec('Al-103', 'Al2SiO5(cr, kyanite)'),
    'Al-104': JanafTableSpec('Al-104', 'Al2SiO5(cr, sillimanite)'),
    # No Ca-silicate reaction: this harvest has no CaSiO3 or Ca2SiO4 table.
    # Fallback names the crystal so a later unary score is not band-less.
    'Ca-027': JanafTableSpec('Ca-027', 'CaO(cr)', 'lime'),
}


# Engine ``quartz`` spans JANAF alpha and beta: O-037's title is quartz
# and both engine quartz phases already carry the alpha-beta transition.
# Cristobalite high/low are separate titled tables and are not listed.
# MgSiO3 i/ii/iii are not listed: the printed table does not name them
# clino, ortho, or proto.
SAME_MINERAL_SUBFORMS: Mapping[str, frozenset] = {
    'quartz': frozenset({'alpha', 'beta'}),
    # Al-096 title resolves to corundum; the printed marker is ALPHA.
    'corundum': frozenset({'alpha'}),
}

_PHASE_CHANGE_RE = re.compile(r'^\s*(.+?)\s*<-->\s*(.+?)\s*$')


@dataclass(frozen=True)
class PolymorphBand:
    """One printed polymorph on a half-open temperature interval.

    ``t_min_K`` is inclusive, ``t_max_K`` exclusive.  None is an open end.
    The departing phase owns temperatures below the marker; the marker
    temperature itself belongs to the arriving phase.  Scored value rows
    do not sit on the marker (those lines are in ``parse_ambiguities``).
    """

    polymorph: str
    t_min_K: Optional[float]
    t_max_K: Optional[float]
    source: str


@dataclass(frozen=True)
class TablePolymorphBands:
    title_polymorph: Optional[str]
    bands: Tuple[PolymorphBand, ...]


def _canonical_transition_side(formula: str, side: str) -> Optional[str]:
    token = JANAF_TRANSITION_POLYMORPHS.get(' '.join(side.upper().split()))
    if token is None:
        return None
    return canonicalize_janaf_transition(formula, token).value


def _title_polymorph(table: Mapping) -> Optional[str]:
    entry = table.get('index_entry') or {}
    tag, token, _reason = resolve_janaf_polymorph(
        formula=str(entry.get('formula_normalised') or ''),
        phase_is_crystal=str(entry.get('state') or '') == 'cr',
        name=str(entry.get('name') or ''),
        title=str(table.get('title_as_published') or ''),
    )
    if tag is StateTag.VALUE and token is not None:
        return token.value
    return None


def printed_polymorph_bands(
    document: Mapping, *, fallback_polymorph: Optional[str] = None
) -> TablePolymorphBands:
    """Bands from this table's printed ``LEFT <--> RIGHT`` markers.

    Solid-solid sides become one band each (SiO2 I/II already alias to
    alpha/beta).  A side that is not a crystal token and a LIQUID right
    side closes the title/fallback mineral (``CRYSTAL <--> LIQUID``).
    A table with no such marker is one band: the printed title polymorph,
    or ``fallback_polymorph`` when the title names none.
    """

    table = document.get('table') or {}
    formula = str((table.get('index_entry') or {}).get('formula_normalised') or '')
    title_polymorph = _title_polymorph(table)
    splits: list[tuple[float, Optional[str], Optional[str], str]] = []
    melt_caps: list[float] = []
    for item in table.get('parse_ambiguities') or ():
        if not isinstance(item, Mapping):
            continue
        raw = str(item.get('raw_line') or '')
        fields = raw.split('\t')
        while fields and fields[-1] == '':
            fields.pop()
        if len(fields) < 2:
            continue
        label = fields[-1].strip()
        match = _PHASE_CHANGE_RE.fullmatch(label)
        if match is None:
            continue
        try:
            temperature = float(fields[0].split()[0])
        except ValueError:
            continue
        left = _canonical_transition_side(formula, match.group(1))
        right_raw = match.group(2).strip()
        right = _canonical_transition_side(formula, right_raw)
        right_is_liquid = right_raw.upper() == 'LIQUID'
        if left is not None and (right is not None or right_is_liquid):
            splits.append((temperature, left, right, label))
        elif left is None and right is None and right_is_liquid:
            melt_caps.append(temperature)
    splits.sort(key=lambda item: item[0])
    if splits:
        bands = []
        first_T, first_left, _first_right, first_label = splits[0]
        if first_left is not None:
            bands.append(PolymorphBand(first_left, None, first_T, first_label))
        for index, (temperature, _left, right, label) in enumerate(splits):
            if right is None:
                continue
            next_T = splits[index + 1][0] if index + 1 < len(splits) else None
            bands.append(PolymorphBand(right, temperature, next_T, label))
        return TablePolymorphBands(title_polymorph, tuple(bands))
    polymorph = title_polymorph or fallback_polymorph
    if polymorph is None:
        return TablePolymorphBands(title_polymorph, ())
    t_max = min(melt_caps) if melt_caps else None
    source = (
        'printed title; crystal band closed by CRYSTAL <--> LIQUID'
        if t_max is not None
        else 'printed title; no solid-solid marker'
    )
    return TablePolymorphBands(
        title_polymorph,
        (PolymorphBand(polymorph, None, t_max, source),),
    )


@lru_cache(maxsize=None)
def bands_for_table_id(table_id: str) -> TablePolymorphBands:
    spec = JANAF_TABLES[table_id]
    return printed_polymorph_bands(
        load_janaf_table(table_id),
        fallback_polymorph=spec.fallback_polymorph,
    )


def _band_contains(band: PolymorphBand, temperature_K: float) -> bool:
    if band.t_min_K is not None and temperature_K < band.t_min_K:
        return False
    if band.t_max_K is not None and temperature_K >= band.t_max_K:
        return False
    return True


def _band_polymorph_matches(
    engine_polymorph: str,
    band: PolymorphBand,
    title_polymorph: Optional[str],
) -> bool:
    if engine_polymorph == band.polymorph:
        return True
    subforms = SAME_MINERAL_SUBFORMS.get(title_polymorph or '')
    return bool(
        title_polymorph
        and engine_polymorph == title_polymorph
        and subforms
        and band.polymorph in subforms
    )


def matching_band(
    engine_polymorph: Optional[str],
    info: TablePolymorphBands,
    temperature_K: float,
) -> Optional[PolymorphBand]:
    if engine_polymorph is None:
        return None
    for band in info.bands:
        if not _band_contains(band, temperature_K):
            continue
        if _band_polymorph_matches(engine_polymorph, band, info.title_polymorph):
            return band
    return None


def _band_edges(info: TablePolymorphBands) -> str:
    def edge(value: Optional[float]) -> str:
        return '-inf' if value is None else f'{value:g}'

    return ', '.join(
        f'{band.polymorph} [{edge(band.t_min_K)}, {edge(band.t_max_K)})'
        for band in info.bands
    ) or 'none'


@dataclass(frozen=True)
class JanafValues:
    """One printed JANAF row.  G/H-formation values in kJ/mol as printed."""

    temperature_K: float
    Cp_J_K_mol: Optional[float]
    S_J_K_mol: Optional[float]
    enthalpy_increment_kJ_mol: Optional[float]  # H(T) - H(298.15)
    formation_gibbs_kJ_mol: Optional[float]
    formation_enthalpy_kJ_mol: Optional[float]


def janaf_values_at(
    document: Mapping, temperature_K: float
) -> Optional[JanafValues]:
    """The printed row at exactly ``temperature_K``; None when not printed."""

    table = document.get('table') or {}
    for row in table.get('values') or []:
        temperature = (row.get('temperature') or {}).get('value')
        if temperature is None or float(temperature) != float(temperature_K):
            continue

        def cell(key: str) -> Optional[float]:
            value = (row.get(key) or {}).get('value')
            return None if value is None else float(value)

        return JanafValues(
            temperature_K=float(temperature),
            Cp_J_K_mol=cell('heat_capacity'),
            S_J_K_mol=cell('entropy'),
            enthalpy_increment_kJ_mol=cell('enthalpy_increment'),
            formation_gibbs_kJ_mol=cell('formation_gibbs_energy'),
            formation_enthalpy_kJ_mol=cell('formation_enthalpy'),
        )
    return None


def load_janaf_table(
    table_id: str, tables_dir: Optional[Path] = None
) -> Mapping:
    """Convenience loader: document for one table id from the compilation."""

    return load_table_document(
        Path(tables_dir or TABLES_DIR) / f'{table_id}.yaml'
    )


# ------------------------------------------------------------ arithmetic --

def reaction_sum(terms: Iterable[Tuple[float, float]]) -> float:
    """Sum ``nu_i * x_i`` over (stoichiometric coefficient, value) pairs.

    DERIVATION for the Gibbs use of this sum: for a balanced reaction the
    per-element reference offsets cancel exactly (module docstring), so the
    same sum applied to engine apparent-G values (kJ/mol) and to JANAF
    Delta_fG(T) values (kJ/mol) yields one physical Delta_rG(T) per side.
    The two sides may be differenced; single-phase values may not be.
    """

    total = 0.0
    for nu, value in terms:
        total += nu * value
    return total


def element_reference_g_kJ_mol(
    enthalpy_increment_kJ_mol: float,
    entropy_J_K_mol: float,
    temperature_K: float,
) -> float:
    """Element reference Gibbs energy on the H(298.15) = 0 convention.

    g_ref(T) = [H(T) - H(298.15)] - T*S(T), in kJ/mol.  H increment is
    kJ/mol as printed by JANAF; S is J/(K mol).  At 298.15 K the enthalpy
    increment is 0, so g_ref = -T*S.
    """

    return enthalpy_increment_kJ_mol - temperature_K * entropy_J_K_mol / 1000.0


def formation_g_from_apparent_kJ_mol(
    apparent_g_kJ_mol: float,
    element_counts: Mapping[str, float],
    element_reference_g_kJ_mol_by_element: Mapping[str, float],
) -> float:
    """JANAF Delta_fG(T) from an apparent Gibbs energy, kJ/mol.

    DERIVATION.  Engines return the apparent Gibbs energy, with the
    elements referenced only at 298.15 K:

        G_a(T) = dfH(298.15) + int_298.15^T Cp dT - T*S(T)
               = H_phase(T) - T*S_phase(T)

    because H_el(298.15) = 0, so H_phase(298.15) = dfH(298.15).

    JANAF Delta_fG(T) references each element in its reference state at
    T (ELEMENT_REFERENCE_TABLES: crystal, liquid, or gas):

        dfG(T) = G_a(T)
                 - sum_el n_el * ([H_el(T) - H_el(298.15)] - T*S_el(T))
               = G_a(T) - sum_el n_el * g_ref_el(T)

    Oxygen's reference species is O2, so n_O2 is half the oxygen-atom
    count.  A missing element is a KeyError, never a zero.

    At T = 298.15 the element enthalpy increments are zero and
    G_a = dfH(298.15) - T*S_phase, so the expression reduces to

        dfG(298.15) = dfH(298.15) - 298.15*(S_phase - sum_el n_el*S_el)

    For a balanced reaction the element sum cancels, and the stoichiometric
    sum of (dfG_engine - dfG_JANAF) equals the apparent-G reaction residual.
    """

    correction = 0.0
    for element, count in element_counts.items():
        correction += count * element_reference_g_kJ_mol_by_element[element]
    return apparent_g_kJ_mol - correction


def enthalpy_increment_kJ_mol(H_T_J_mol: float, H_298_J_mol: float) -> float:
    """H(T) - H(298.15) in kJ/mol from two same-convention engine H values."""

    return (H_T_J_mol - H_298_J_mol) / 1000.0


def residual_pct(residual: float, reference: float) -> Optional[float]:
    if reference == 0.0:
        return None
    return 100.0 * residual / reference


# ------------------------------------------------------------- row types --

@dataclass(frozen=True)
class PropertyResidual:
    """One convention-free property of one phase at one T vs JANAF."""

    property: str  # 'S' | 'Cp' | 'H_increment'
    engine: Optional[float]
    janaf: Optional[float]
    residual: Optional[float]
    residual_pct: Optional[float]
    units: str
    absence_reason: Optional[str] = None  # typed-absence token when refused-by-engine


@dataclass(frozen=True)
class ReactionTermDetail:
    role: str
    nu: float
    engine_phase_id: str
    engine_polymorph: Optional[str]
    janaf_table_id: str
    janaf_polymorph: str
    engine_G_kJ_mol: float
    janaf_dfG_kJ_mol: float


@dataclass(frozen=True)
class ReactionRow:
    engine: str
    temperature_K: float
    reaction_id: str
    equation: str
    # None when the reaction has no SiO2 term. Never an empty string.
    sio2_polymorph: Optional[str]
    drG_engine_kJ_mol: float
    drG_janaf_kJ_mol: float
    residual_kJ_mol: float  # engine - JANAF
    terms: Tuple[ReactionTermDetail, ...]
    notes: Tuple[str, ...] = ()
    # ThermoEngine-only variant with the MELTS quartz correction removed
    # (MELTS_QUARTZ_ADJUSTMENT_KJ_MOL); None where N/A (MAGEMin rows).
    drG_engine_no_quartz_adjustment_kJ_mol: Optional[float] = None
    residual_no_quartz_adjustment_kJ_mol: Optional[float] = None
    kind: str = 'reaction'
    status: str = STATUS_SCORED


@dataclass(frozen=True)
class PhaseRow:
    engine: str
    phase_id: str
    polymorph: Optional[str]
    janaf_table_id: str
    janaf_polymorph: str
    temperature_K: float
    properties: Tuple[PropertyResidual, ...]
    notes: Tuple[str, ...] = ()
    kind: str = 'phase_property'
    status: str = STATUS_SCORED


@dataclass(frozen=True)
class RefusalRow:
    """A planned row refused typed before/without scoring (never a zero)."""

    reason: str  # one of the tokens above
    detail: str
    engine: Optional[str] = None
    phase_id: Optional[str] = None
    reaction_id: Optional[str] = None
    janaf_table_id: Optional[str] = None
    temperature_K: Optional[float] = None
    engine_polymorph: Optional[str] = None
    janaf_polymorph: Optional[str] = None
    kind: str = 'refusal'
    status: str = STATUS_REFUSED


# ----------------------------------------------------- polymorph contract --

def engine_phase_polymorph(engine: str, phase_id: str) -> Optional[str]:
    """Polymorph label from the engine's own phase map (not restated here)."""

    if engine == 'magemin':
        spec = _MAGEMIN_PURE_PHASES.get(phase_id)
        return spec.polymorph if spec is not None else None
    if engine == 'thermoengine':
        return _TE_PURE_PHASE_POLYMORPH.get(phase_id)
    return None


def require_polymorph_match(
    engine_polymorph: Optional[str],
    janaf: JanafTableSpec,
    *,
    temperature_K: float,
    context: str,
) -> PolymorphBand:
    """The printed band at T, or a typed refusal when the engine is outside it."""

    info = bands_for_table_id(janaf.table_id)
    band = matching_band(engine_polymorph, info, temperature_K)
    if band is None:
        containing = next(
            (
                item.polymorph
                for item in info.bands
                if _band_contains(item, temperature_K)
            ),
            'none',
        )
        raise PolymorphMismatchError(
            f'{context}: engine polymorph {engine_polymorph!r} is not the '
            f'printed JANAF band {containing!r} of table {janaf.table_id} at '
            f'{temperature_K:g} K (bands: {_band_edges(info)}); a row outside '
            f'the matching band is a fake residual'
        )
    return band


def _subform_note(
    engine_polymorph: Optional[str], band: PolymorphBand
) -> Optional[str]:
    """Note when a same-mineral exemption scored a printed sub-form."""

    if engine_polymorph is None or engine_polymorph == band.polymorph:
        return None
    return (
        f"JANAF printed sub-form {band.polymorph} ({band.source}); same "
        f"mineral as engine {engine_polymorph}; the marker is read, and "
        f"alpha/beta inside one quartz phase is not a mismatch"
    )


# ------------------------------------------------------------- the plan --

@dataclass(frozen=True)
class ReactionSpec:
    reaction_id: str
    equation: str
    terms: Tuple[Tuple[str, float], ...]  # (role, nu); products positive
    janaf_table_by_role: Mapping[str, str]
    engine_phase_by_role: Mapping[str, Mapping[str, Optional[str]]]
    # (engine, role, why that role has no phase id). A missing phase id
    # without an entry here is a catalogue bug, not a physics refusal.
    missing_endmember: Tuple[Tuple[str, str, str], ...] = ()


# 2 MgO(cr) + SiO2(cr) -> Mg2SiO4(cr), SiO2 on the QUARTZ basis at all T
# (1500 K row: quartz is metastable for both sides -- flagged, matched).
REACTION_FORSTERITE = ReactionSpec(
    reaction_id='2MgO+SiO2->Mg2SiO4',
    equation='2 MgO(cr) + SiO2(cr, quartz) -> Mg2SiO4(cr, forsterite)',
    terms=(('Mg2SiO4', 1.0), ('MgO', -2.0), ('SiO2', -1.0)),
    janaf_table_by_role={
        'MgO': 'Mg-008',
        'SiO2': 'O-037',
        'Mg2SiO4': 'Mg-028',
    },
    engine_phase_by_role={
        'magemin': {'MgO': 'per', 'SiO2': 'q', 'Mg2SiO4': 'fo'},
        'thermoengine': {'MgO': 'Per', 'SiO2': 'Qz', 'Mg2SiO4': 'Fo'},
    },
)

# MgO(cr) + SiO2(cr) -> MgSiO3(cr).  Mg-012's printed bands are i/ii/iii,
# not clinoenstatite.  Neither TE cEn nor MAGEMin en is one of those
# tokens, so both engines' rows are refused typed.
REACTION_ENSTATITE = ReactionSpec(
    reaction_id='MgO+SiO2->MgSiO3',
    equation='MgO(cr) + SiO2(cr, quartz) -> MgSiO3(cr)',
    terms=(('MgSiO3', 1.0), ('MgO', -1.0), ('SiO2', -1.0)),
    janaf_table_by_role={
        'MgO': 'Mg-008',
        'SiO2': 'O-037',
        'MgSiO3': 'Mg-012',
    },
    engine_phase_by_role={
        'magemin': {'MgO': 'per', 'SiO2': 'q', 'MgSiO3': 'en'},
        'thermoengine': {'MgO': 'Per', 'SiO2': 'Qz', 'MgSiO3': 'cEn'},
    },
)

# MgO + Al2O3 -> MgAl2O4. ThermoEngine Berman symbol is Spl (MgAl2O4).
# MAGEMin ig host spl has no plain MgAl2O4 endmember. nsp is the ordered
# normal spinel of Holland et al. 2018; JANAF Al-089 is calorimetric
# spinel, so nsp is not scored.
_MAGEMIN_SPINEL_REFUSAL = (
    "MAGEMin ig host 'spl' has no plain MgAl2O4 endmember "
    "(nsp, isp, nhc, ihc, nmt, imt, pcr, qndm). nsp is ordered normal "
    "spinel (Holland et al. 2018); JANAF Al-089 is calorimetric spinel, "
    "so nsp is not scored"
)
REACTION_SPINEL = ReactionSpec(
    reaction_id='MgO+Al2O3->MgAl2O4',
    equation='MgO(cr) + Al2O3(cr, alpha) -> MgAl2O4(cr, spinel)',
    terms=(('MgAl2O4', 1.0), ('MgO', -1.0), ('Al2O3', -1.0)),
    janaf_table_by_role={
        'MgO': 'Mg-008',
        'Al2O3': 'Al-096',
        'MgAl2O4': 'Al-089',
    },
    engine_phase_by_role={
        'magemin': {'MgO': 'per', 'Al2O3': 'cor', 'MgAl2O4': None},
        'thermoengine': {'MgO': 'Per', 'Al2O3': 'Crn', 'MgAl2O4': 'Spl'},
    },
    missing_endmember=(
        ('magemin', 'MgAl2O4', _MAGEMIN_SPINEL_REFUSAL),
    ),
)

# Al2O3 + SiO2(quartz) -> Al2SiO5. One row per product polymorph so a
# mismatched engine phase refuses. CaSiO3 / Ca2SiO4 / stoichiometric FeO
# are omitted: this harvest has no CaSiO3 or Ca2SiO4 table, and Fe-001 is
# Fe0.947O (Fe-030 hematite has no stoichiometric FeO partner).
REACTION_ANDALUSITE = ReactionSpec(
    reaction_id='Al2O3+SiO2->Al2SiO5(andalusite)',
    equation='Al2O3(cr, alpha) + SiO2(cr, quartz) -> Al2SiO5(cr, andalusite)',
    terms=(('Al2SiO5', 1.0), ('Al2O3', -1.0), ('SiO2', -1.0)),
    janaf_table_by_role={
        'Al2O3': 'Al-096',
        'SiO2': 'O-037',
        'Al2SiO5': 'Al-102',
    },
    engine_phase_by_role={
        'magemin': {'Al2O3': 'cor', 'SiO2': 'q', 'Al2SiO5': 'and'},
        'thermoengine': {'Al2O3': 'Crn', 'SiO2': 'Qz', 'Al2SiO5': 'And'},
    },
)
REACTION_KYANITE = ReactionSpec(
    reaction_id='Al2O3+SiO2->Al2SiO5(kyanite)',
    equation='Al2O3(cr, alpha) + SiO2(cr, quartz) -> Al2SiO5(cr, kyanite)',
    terms=(('Al2SiO5', 1.0), ('Al2O3', -1.0), ('SiO2', -1.0)),
    janaf_table_by_role={
        'Al2O3': 'Al-096',
        'SiO2': 'O-037',
        'Al2SiO5': 'Al-103',
    },
    engine_phase_by_role={
        'magemin': {'Al2O3': 'cor', 'SiO2': 'q', 'Al2SiO5': 'ky'},
        'thermoengine': {'Al2O3': 'Crn', 'SiO2': 'Qz', 'Al2SiO5': 'Ky'},
    },
)
REACTION_SILLIMANITE = ReactionSpec(
    reaction_id='Al2O3+SiO2->Al2SiO5(sillimanite)',
    equation='Al2O3(cr, alpha) + SiO2(cr, quartz) -> Al2SiO5(cr, sillimanite)',
    terms=(('Al2SiO5', 1.0), ('Al2O3', -1.0), ('SiO2', -1.0)),
    janaf_table_by_role={
        'Al2O3': 'Al-096',
        'SiO2': 'O-037',
        'Al2SiO5': 'Al-104',
    },
    engine_phase_by_role={
        'magemin': {'Al2O3': 'cor', 'SiO2': 'q', 'Al2SiO5': 'sill'},
        'thermoengine': {'Al2O3': 'Crn', 'SiO2': 'Qz', 'Al2SiO5': 'Sil'},
    },
)

REACTION_CATALOGUE: Tuple[ReactionSpec, ...] = (
    REACTION_FORSTERITE,
    REACTION_ENSTATITE,
    REACTION_SPINEL,
    REACTION_ANDALUSITE,
    REACTION_KYANITE,
    REACTION_SILLIMANITE,
)
REACTIONS = REACTION_CATALOGUE
ENGINES = ('magemin', 'thermoengine')


@dataclass(frozen=True)
class PhaseScoreRequest:
    engine: str
    phase_id: str
    janaf_table_id: Optional[str]  # None = no JANAF table for this polymorph
    temperatures_K: Tuple[float, ...] = DEFAULT_TEMPERATURES_K


DEFAULT_PHASE_REQUESTS: Tuple[PhaseScoreRequest, ...] = (
    PhaseScoreRequest('magemin', 'per', 'Mg-008'),
    PhaseScoreRequest('thermoengine', 'Per', 'Mg-008'),
    PhaseScoreRequest('magemin', 'fo', 'Mg-028'),
    PhaseScoreRequest('thermoengine', 'Fo', 'Mg-028'),
    PhaseScoreRequest('magemin', 'q', 'O-037'),
    PhaseScoreRequest('thermoengine', 'Qz', 'O-037'),
    PhaseScoreRequest('magemin', 'cor', 'Al-096'),
    PhaseScoreRequest('thermoengine', 'Crn', 'Al-096'),
    # No MAGEMin spinel request: host spl has no justified endmember
    # (see REACTION_SPINEL.missing_endmember).
    PhaseScoreRequest('thermoengine', 'Spl', 'Al-089'),
    PhaseScoreRequest('magemin', 'and', 'Al-102'),
    PhaseScoreRequest('thermoengine', 'And', 'Al-102'),
    PhaseScoreRequest('magemin', 'ky', 'Al-103'),
    PhaseScoreRequest('thermoengine', 'Ky', 'Al-103'),
    PhaseScoreRequest('magemin', 'sill', 'Al-104'),
    PhaseScoreRequest('thermoengine', 'Sil', 'Al-104'),
    # cEn / En / en are clino or ortho.  Mg-012's printed bands are
    # i/ii/iii, so all three requests are refused at every default T.
    PhaseScoreRequest('thermoengine', 'cEn', 'Mg-012'),
    PhaseScoreRequest('thermoengine', 'En', 'Mg-012'),
    PhaseScoreRequest('magemin', 'en', 'Mg-012'),
    # Andalusite phase vs the sillimanite table: polymorph refuse.
    PhaseScoreRequest('thermoengine', 'And', 'Al-104'),
    PhaseScoreRequest('magemin', 'and', 'Al-104'),
    # This JANAF compilation has no tridymite table; trd is the stable
    # MAGEMin silica polymorph at 1500 K, so the gap is real and typed.
    PhaseScoreRequest('magemin', 'trd', None, (1500.0,)),
)


def preflight_phase_request(
    request: PhaseScoreRequest, *, temperature_K: float
) -> Optional[RefusalRow]:
    """Refuse one phase row at one T when the polymorph band does not match."""

    engine_polymorph = engine_phase_polymorph(request.engine, request.phase_id)
    if request.janaf_table_id is None:
        return RefusalRow(
            reason=NO_JANAF_TABLE_FOR_POLYMORPH,
            detail=(
                f'engine {request.engine} phase {request.phase_id!r} is '
                f'{engine_polymorph!r}; this JANAF compilation has no '
                f'{engine_polymorph} table -- no reference, no residual'
            ),
            engine=request.engine,
            phase_id=request.phase_id,
            temperature_K=temperature_K,
            engine_polymorph=engine_polymorph,
        )
    janaf = JANAF_TABLES[request.janaf_table_id]
    try:
        require_polymorph_match(
            engine_polymorph,
            janaf,
            temperature_K=temperature_K,
            context=f'{request.engine} {request.phase_id}',
        )
    except PolymorphMismatchError as exc:
        info = bands_for_table_id(request.janaf_table_id)
        containing = next(
            (
                item.polymorph
                for item in info.bands
                if _band_contains(item, temperature_K)
            ),
            None,
        )
        return RefusalRow(
            reason=POLYMORPH_MISMATCH,
            detail=str(exc),
            engine=request.engine,
            phase_id=request.phase_id,
            janaf_table_id=request.janaf_table_id,
            temperature_K=temperature_K,
            engine_polymorph=engine_polymorph,
            janaf_polymorph=containing,
        )
    return None


def preflight_reaction(
    reaction: ReactionSpec, engine: str, *, temperature_K: float
) -> Optional[RefusalRow]:
    """Refuse one reaction row at one T when any term's band does not match."""

    phase_by_role = reaction.engine_phase_by_role.get(engine)
    if phase_by_role is None:
        return RefusalRow(
            reason=POLYMORPH_MISMATCH,
            detail=f'no {engine} phase map for {reaction.reaction_id}',
            engine=engine,
            reaction_id=reaction.reaction_id,
            temperature_K=temperature_K,
        )
    for role, _nu in reaction.terms:
        phase_id = phase_by_role.get(role)
        if phase_id is None:
            detail = next(
                (
                    text
                    for eng, role_name, text in reaction.missing_endmember
                    if eng == engine and role_name == role
                ),
                None,
            )
            if detail is None:
                raise KeyError(
                    f'{reaction.reaction_id} has no {engine} phase id for '
                    f'{role} and no missing_endmember entry'
                )
            return RefusalRow(
                reason=NO_JUSTIFIED_ENGINE_ENDMEMBER,
                detail=detail,
                engine=engine,
                reaction_id=reaction.reaction_id,
                janaf_table_id=reaction.janaf_table_by_role[role],
                temperature_K=temperature_K,
            )
        janaf = JANAF_TABLES[reaction.janaf_table_by_role[role]]
        engine_polymorph = engine_phase_polymorph(engine, phase_id)
        try:
            require_polymorph_match(
                engine_polymorph,
                janaf,
                temperature_K=temperature_K,
                context=f'{engine} {phase_id} (role {role})',
            )
        except PolymorphMismatchError as exc:
            info = bands_for_table_id(janaf.table_id)
            containing = next(
                (
                    item.polymorph
                    for item in info.bands
                    if _band_contains(item, temperature_K)
                ),
                None,
            )
            return RefusalRow(
                reason=POLYMORPH_MISMATCH,
                detail=str(exc),
                engine=engine,
                reaction_id=reaction.reaction_id,
                phase_id=phase_id,
                janaf_table_id=janaf.table_id,
                temperature_K=temperature_K,
                engine_polymorph=engine_polymorph,
                janaf_polymorph=containing,
            )
    return None


# ----------------------------------------------------------- row builders --

EngineQuery = Callable[[str, float], PurePhaseProperties]
JanafQuery = Callable[[str, float], Optional[JanafValues]]


def _quartz_notes(reaction_or_phase_T: float) -> Tuple[str, ...]:
    if reaction_or_phase_T > QUARTZ_METASTABLE_ABOVE_K:
        return (
            'quartz is metastable above the q->trd transition (~1143 K at '
            '1 bar); both sides use the quartz assessment/extension -- '
            'polymorphs matched, stability flagged',
        )
    return ()


def _al2sio5_product_polymorph(reaction: ReactionSpec) -> Optional[str]:
    table_id = reaction.janaf_table_by_role.get('Al2SiO5')
    if table_id is None:
        return None
    return bands_for_table_id(table_id).title_polymorph


def _al2sio5_stability_notes(
    polymorph: Optional[str], temperature_K: float
) -> Tuple[str, ...]:
    """1-bar metastability flags. Matched polymorphs still score."""

    if polymorph is None:
        return ()
    notes = []
    if polymorph == 'kyanite' and temperature_K > KYANITE_METASTABLE_ABOVE_K:
        notes.append(
            'kyanite is metastable at 1 bar above the andalusite-kyanite '
            'intercept (~493 K / ~220 C; Holdaway 1971 family); polymorph '
            'matched, stability flagged'
        )
    if (
        polymorph == 'sillimanite'
        and temperature_K < ANDALUSITE_SILLIMANITE_1BAR_K
    ):
        notes.append(
            'sillimanite is metastable at 1 bar below the '
            'andalusite-sillimanite transition (~1043 K / 770 C, Holdaway '
            '1971); polymorph matched, stability flagged'
        )
    if (
        polymorph == 'andalusite'
        and temperature_K > ANDALUSITE_SILLIMANITE_1BAR_K
    ):
        notes.append(
            'andalusite is metastable at 1 bar above the '
            'andalusite-sillimanite transition (~1043 K / 770 C, Holdaway '
            '1971); polymorph matched, stability flagged'
        )
    if (
        polymorph in ('andalusite', 'kyanite', 'sillimanite')
        and temperature_K > AL2SIO5_MULLITE_ABOVE_K
    ):
        notes.append(
            'Al2SiO5 is metastable at 1 bar above the mullite + silica '
            'boundary (~1473 K / 1200 C); polymorph matched, stability '
            'flagged'
        )
    return tuple(notes)


def sio2_polymorph_for_reaction(reaction: ReactionSpec) -> Optional[str]:
    """Title mineral of the SiO2 term, or None when the reaction has none."""

    table_id = reaction.janaf_table_by_role.get('SiO2')
    if table_id is None:
        return None
    return bands_for_table_id(table_id).title_polymorph


def build_reaction_row(
    reaction: ReactionSpec,
    engine: str,
    temperature_K: float,
    *,
    engine_query: EngineQuery,
    janaf_query: JanafQuery,
) -> ReactionRow:
    """One Delta_rG(T) residual row.

    ``engine_query(phase_id, T)`` -> PurePhaseProperties (apparent G);
    ``janaf_query(table_id, T)`` -> JanafValues (printed Delta_fG).
    """

    phase_by_role = reaction.engine_phase_by_role[engine]
    details = []
    engine_terms = []
    janaf_terms = []
    for role, nu in reaction.terms:
        phase_id = phase_by_role[role]
        table_id = reaction.janaf_table_by_role[role]
        janaf_spec = JANAF_TABLES[table_id]
        props = engine_query(phase_id, temperature_K)
        # Live re-check: the RETURNED polymorph must sit in the printed
        # band at this T, not just the static map.
        band = require_polymorph_match(
            props.polymorph,
            janaf_spec,
            temperature_K=temperature_K,
            context=f'{engine} {phase_id} live at {temperature_K:g} K',
        )
        if props.G_J_mol is None:
            raise ValueError(
                f'{engine} {phase_id} G is absent at {temperature_K} K; '
                'reaction sum cannot be formed (absence is never a zero)'
            )
        janaf_values = janaf_query(table_id, temperature_K)
        if janaf_values is None or janaf_values.formation_gibbs_kJ_mol is None:
            raise ValueError(
                f'JANAF table {table_id} has no printed Delta_fG row at '
                f'{temperature_K} K'
            )
        engine_g_kJ = props.G_J_mol / 1000.0
        details.append(
            ReactionTermDetail(
                role=role,
                nu=nu,
                engine_phase_id=phase_id,
                engine_polymorph=props.polymorph,
                janaf_table_id=table_id,
                janaf_polymorph=band.polymorph,
                engine_G_kJ_mol=engine_g_kJ,
                janaf_dfG_kJ_mol=janaf_values.formation_gibbs_kJ_mol,
            )
        )
        engine_terms.append((nu, engine_g_kJ))
        janaf_terms.append((nu, janaf_values.formation_gibbs_kJ_mol))
    drg_engine = reaction_sum(engine_terms)
    drg_janaf = reaction_sum(janaf_terms)
    notes: list[str] = []
    if reaction.janaf_table_by_role.get('SiO2') == 'O-037':
        notes.extend(_quartz_notes(temperature_K))
    notes.extend(
        _al2sio5_stability_notes(
            _al2sio5_product_polymorph(reaction), temperature_K
        )
    )
    for detail in details:
        if detail.engine_polymorph != detail.janaf_polymorph:
            notes.append(
                f'{detail.role}: JANAF printed sub-form '
                f'{detail.janaf_polymorph} at {temperature_K:g} K; same '
                f'mineral as engine {detail.engine_polymorph}'
            )
    drg_no_adj: Optional[float] = None
    residual_no_adj: Optional[float] = None
    if engine == 'thermoengine':
        for role, nu in reaction.terms:
            if phase_by_role[role] == 'Qz':
                # As-run G includes the adjustment: G_run = G_wo + ADJ, so
                # the no-adjustment sum subtracts nu_Qz * ADJ from the total.
                drg_no_adj = drg_engine - nu * MELTS_QUARTZ_ADJUSTMENT_KJ_MOL
                residual_no_adj = drg_no_adj - drg_janaf
        if drg_no_adj is not None:
            notes.append(
                'TE quartz G includes the MELTS QUARTZ_ADJUSTMENT '
                '(-1.291 kJ/mol; upstream BermanStoichiometricPhases.m, '
                'default on); the no-adjustment variant is reported in its '
                'own columns'
            )
    return ReactionRow(
        engine=engine,
        temperature_K=temperature_K,
        reaction_id=reaction.reaction_id,
        equation=reaction.equation,
        sio2_polymorph=sio2_polymorph_for_reaction(reaction),
        drG_engine_kJ_mol=drg_engine,
        drG_janaf_kJ_mol=drg_janaf,
        residual_kJ_mol=drg_engine - drg_janaf,
        terms=tuple(details),
        notes=tuple(notes),
        drG_engine_no_quartz_adjustment_kJ_mol=drg_no_adj,
        residual_no_quartz_adjustment_kJ_mol=residual_no_adj,
    )


def build_phase_row(
    request: PhaseScoreRequest,
    temperature_K: float,
    *,
    engine_query: EngineQuery,
    janaf_query: JanafQuery,
) -> PhaseRow:
    """One phase's S/Cp/H(T)-H(298.15) residual row vs JANAF.

    Engine typed absences (MAGEMin non-stable phases) are carried per
    property with their reason -- never substituted by a number.
    """

    janaf_spec = JANAF_TABLES[request.janaf_table_id or '']
    props = engine_query(request.phase_id, temperature_K)
    band = require_polymorph_match(
        props.polymorph,
        janaf_spec,
        temperature_K=temperature_K,
        context=(
            f'{request.engine} {request.phase_id} live at {temperature_K:g} K'
        ),
    )
    janaf_values = janaf_query(request.janaf_table_id or '', temperature_K)
    if janaf_values is None:
        raise ValueError(
            f'JANAF table {request.janaf_table_id} has no printed row at '
            f'{temperature_K} K'
        )
    props_298 = (
        props
        if float(temperature_K) == 298.15
        else engine_query(request.phase_id, 298.15)
    )
    absence_by_property = {a.property: a.reason for a in props.absences}
    conflicted = [
        name
        for name, value in (
            ('S', props.S_J_K_mol),
            ('Cp', props.Cp_J_K_mol),
            ('H', props.H_J_mol),
        )
        if value is not None and name in absence_by_property
    ]

    def entry(
        prop: str,
        engine_value: Optional[float],
        janaf_value: Optional[float],
        units: str,
    ) -> PropertyResidual:
        # PropertyAbsence tokens use 'H'; the residual row names the derived
        # quantity 'H_increment' -- same absence, convention-free basis.
        # A number alongside an absence token is not scored: the absence wins.
        absence_prop = 'H' if prop == 'H_increment' else prop
        absence_reason = absence_by_property.get(absence_prop)
        if absence_reason is not None or engine_value is None:
            return PropertyResidual(
                property=prop,
                engine=None,
                janaf=janaf_value,
                residual=None,
                residual_pct=None,
                units=units,
                absence_reason=absence_reason or 'absent',
            )
        if janaf_value is None:
            return PropertyResidual(
                property=prop,
                engine=engine_value,
                janaf=None,
                residual=None,
                residual_pct=None,
                units=units,
                absence_reason=NO_JANAF_ROW_AT_T,
            )
        residual = engine_value - janaf_value
        return PropertyResidual(
            property=prop,
            engine=engine_value,
            janaf=janaf_value,
            residual=residual,
            residual_pct=residual_pct(residual, janaf_value),
            units=units,
        )

    h_increment_engine: Optional[float]
    if (
        'H' in absence_by_property
        or props.H_J_mol is None
        or props_298.H_J_mol is None
    ):
        h_increment_engine = None
    else:
        h_increment_engine = enthalpy_increment_kJ_mol(
            props.H_J_mol, props_298.H_J_mol
        )
    properties = (
        entry('S', props.S_J_K_mol, janaf_values.S_J_K_mol, 'J/(K mol)'),
        entry('Cp', props.Cp_J_K_mol, janaf_values.Cp_J_K_mol, 'J/(K mol)'),
        entry(
            'H_increment',
            h_increment_engine,
            janaf_values.enthalpy_increment_kJ_mol,
            'kJ/mol',
        ),
    )
    notes = list(
        _quartz_notes(temperature_K) if props.polymorph == 'quartz' else ()
    )
    notes.extend(_al2sio5_stability_notes(props.polymorph, temperature_K))
    subform = _subform_note(props.polymorph, band)
    if subform is not None:
        notes.append(subform)
    if conflicted:
        notes.append(
            'engine returned a number and a PropertyAbsence for '
            + ', '.join(conflicted)
            + '; the absence wins and the number is not scored'
        )
    for warning in props.warnings:
        if warning not in notes:
            notes.append(warning)
    return PhaseRow(
        engine=request.engine,
        phase_id=request.phase_id,
        polymorph=props.polymorph,
        janaf_table_id=janaf_spec.table_id,
        janaf_polymorph=band.polymorph,
        temperature_K=temperature_K,
        properties=properties,
        notes=tuple(notes),
    )
