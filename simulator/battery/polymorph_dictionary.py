"""Canonical (formula, printed qualifier) → Polymorph dictionary.

The key is always the pair. ``Alpha`` alone is not a substance. Ion, cis/trans,
and pressure qualifiers are classified here and are never crystal tokens.
"""

from __future__ import annotations

import re
from simulator.battery.enums import Polymorph, PrintedQualifierKind, StateTag

_TRAILING_FORMULA_RE = re.compile(r"\s*\([^()]*(?:\([^()]*\)[^()]*)*\)\s*$")
_PRESSURE_QUALIFIER_RE = re.compile(r"^\d+ Bar$")
_ION_QUALIFIER = "Ion"
_ISOMER_QUALIFIERS = frozenset({"Cis", "Trans"})

# (formula_normalised, printed qualifier, canonical token, table ids).
# Census of every JANAF table that prints a crystal qualifier (74 tables, 73 keys).
# Al2O3 + Alpha is corundum so it identity-equals USGS B1544 Corundum.
_JANAF_CRYSTAL_ROWS: tuple[tuple[str, str, Polymorph, tuple[str, ...]], ...] = (
    ("Al2O3", "Alpha", Polymorph.CORUNDUM, ("Al-096",)),
    ("Al2O3", "Delta", Polymorph.DELTA, ("Al-097",)),
    ("Al2O3", "Gamma", Polymorph.GAMMA, ("Al-098",)),
    ("Al2O3", "Kappa", Polymorph.KAPPA, ("Al-099",)),
    ("Al2SiO5", "Andalusite", Polymorph.ANDALUSITE, ("Al-102",)),
    ("Al2SiO5", "Kyanite", Polymorph.KYANITE, ("Al-103",)),
    ("Al2SiO5", "Sillimanite", Polymorph.SILLIMANITE, ("Al-104",)),
    ("Al6Si2O13", "Mullite", Polymorph.MULLITE, ("Al-112",)),
    ("B", "Beta-Rhombohedral", Polymorph.BETA_RHOMBOHEDRAL, ("B-002",)),
    ("BaH2O2", "Alpha", Polymorph.ALPHA, ("Ba-025",)),
    ("Ca", "Alpha", Polymorph.ALPHA, ("Ca-002",)),
    ("Ca", "Beta", Polymorph.BETA, ("Ca-003",)),
    ("Cs2SO4", "I", Polymorph.I, ("Cs-022",)),
    ("Cs2SO4", "II", Polymorph.II, ("Cs-023",)),
    ("Fe", "Alpha-Delta", Polymorph.ALPHA_DELTA, ("Fe-004",)),
    ("Fe", "Gamma", Polymorph.GAMMA, ("Fe-005",)),
    ("Fe0.877S", "Pyrrhotite", Polymorph.PYRRHOTITE, ("Fe-002",)),
    ("Fe0.947O", "Wustite", Polymorph.WUSTITE, ("Fe-001",)),
    ("Fe2O3", "Hematite", Polymorph.HEMATITE, ("Fe-030",)),
    ("Fe3O4", "Magnetite", Polymorph.MAGNETITE, ("Fe-032",)),
    ("FeS", "Troilite", Polymorph.TROILITE, ("Fe-023", "Fe-025")),
    ("FeS2", "Marcasite", Polymorph.MARCASITE, ("Fe-027",)),
    ("FeS2", "Pyrite", Polymorph.PYRITE, ("Fe-028",)),
    ("H10O8S", "Tetrahydrate", Polymorph.TETRAHYDRATE, ("H-096",)),
    ("H15O10.5S", "Hemihexahydrate", Polymorph.HEMIHEXAHYDRATE, ("H-097",)),
    ("H4O5S", "Monohydrate", Polymorph.MONOHYDRATE, ("H-092",)),
    ("H6O6S", "Dihydrate", Polymorph.DIHYDRATE, ("H-094",)),
    ("H8O7S", "Trihydrate", Polymorph.TRIHYDRATE, ("H-095",)),
    ("Hf", "Alpha", Polymorph.ALPHA, ("Hf-002",)),
    ("Hf", "Beta", Polymorph.BETA, ("Hf-003",)),
    ("K2SO4", "Alpha", Polymorph.ALPHA, ("K-017",)),
    ("K2SO4", "Beta", Polymorph.BETA, ("K-018",)),
    ("Li2SO4", "Alpha", Polymorph.ALPHA, ("Li-026",)),
    ("Li2SO4", "Beta", Polymorph.BETA, ("Li-027",)),
    ("MoI2", "Alpha", Polymorph.ALPHA, ("I-034",)),
    ("Na2S2", "Beta", Polymorph.BETA, ("Na-034",)),
    ("Na2SO4", "Delta", Polymorph.DELTA, ("Na-019",)),
    ("Na2SO4", "I", Polymorph.I, ("Na-020",)),
    ("Na2SO4", "III", Polymorph.III, ("Na-021",)),
    ("Na2SO4", "IV", Polymorph.IV, ("Na-022",)),
    ("Na2SO4", "V", Polymorph.V, ("Na-023",)),
    ("Na3AlF6", "Alpha", Polymorph.ALPHA, ("Al-052",)),
    ("Na3AlF6", "Beta", Polymorph.BETA, ("Al-053",)),
    ("P", "Black", Polymorph.BLACK, ("P-007",)),
    ("P", "Red, IV", Polymorph.RED_IV, ("P-005",)),
    ("P", "Red, V", Polymorph.RED_V, ("P-006",)),
    ("P", "White", Polymorph.WHITE, ("P-002",)),
    ("PbF2", "Alpha", Polymorph.ALPHA, ("F-091",)),
    ("PbF2", "Beta", Polymorph.BETA, ("F-092",)),
    ("PbO", "Red", Polymorph.RED, ("O-005",)),
    ("PbO", "Yellow", Polymorph.YELLOW, ("O-006",)),
    ("S", "Monoclinic", Polymorph.MONOCLINIC, ("S-003",)),
    ("S", "Orthorhombic", Polymorph.ORTHORHOMBIC, ("S-002",)),
    ("Si3N4", "Alpha", Polymorph.ALPHA, ("N-035",)),
    ("SiC", "Alpha", Polymorph.ALPHA, ("C-100",)),
    ("SiC", "Beta", Polymorph.BETA, ("C-101",)),
    ("SiO2", "Cristobalite, High", Polymorph.CRISTOBALITE_HIGH, ("O-035",)),
    ("SiO2", "Cristobalite, Low", Polymorph.CRISTOBALITE_LOW, ("O-036",)),
    ("SiO2", "Quartz", Polymorph.QUARTZ, ("O-037",)),
    ("Sr", "Alpha", Polymorph.ALPHA, ("Sr-002",)),
    ("Sr", "Beta", Polymorph.BETA, ("Sr-003",)),
    ("Ti", "Alpha", Polymorph.ALPHA, ("Ti-002",)),
    ("Ti", "Beta", Polymorph.BETA, ("Ti-003",)),
    ("Ti3O5", "Alpha", Polymorph.ALPHA, ("O-080",)),
    ("Ti3O5", "Beta", Polymorph.BETA, ("O-081",)),
    ("TiO", "Alpha", Polymorph.ALPHA, ("O-018",)),
    ("TiO", "Beta", Polymorph.BETA, ("O-019",)),
    ("TiO2", "Anatase", Polymorph.ANATASE, ("O-042",)),
    ("TiO2", "Rutile", Polymorph.RUTILE, ("O-043",)),
    ("WCl6", "Alpha", Polymorph.ALPHA, ("Cl-189",)),
    ("WCl6", "Beta", Polymorph.BETA, ("Cl-190",)),
    ("Zr", "Alpha", Polymorph.ALPHA, ("Zr-002",)),
    ("Zr", "Beta", Polymorph.BETA, ("Zr-003",)),
)

JANAF_CRYSTAL_DICTIONARY: dict[tuple[str, str], Polymorph] = {
    (formula, qualifier): token for formula, qualifier, token, _ids in _JANAF_CRYSTAL_ROWS
}

JANAF_CRYSTAL_TABLE_IDS: dict[tuple[str, str], tuple[str, ...]] = {
    (formula, qualifier): ids for formula, qualifier, _token, ids in _JANAF_CRYSTAL_ROWS
}

# Transition-label sides that already split JANAF grids (ALPHA <--> GAMMA, I <--> II).
# IV/V/Kappa are closed dictionary tokens from table names; adding them here
# would newly split tables such as Na-020 (IV <--> I) and change series counts.
JANAF_TRANSITION_POLYMORPHS: dict[str, Polymorph] = {
    "ALPHA": Polymorph.ALPHA,
    "BETA": Polymorph.BETA,
    "GAMMA": Polymorph.GAMMA,
    "DELTA": Polymorph.DELTA,
    "I": Polymorph.I,
    "II": Polymorph.II,
    "III": Polymorph.III,
}

# (formula, transition-label token) → canonical token.
# JANAF O-037 prints "Silicon Oxide, Quartz" and splits on "I <--> II".
# B1544 prints Alpha quartz / Beta quartz. Roman I/II are JANAF's generic
# numbering of those two forms, not a different identity. Canonical is
# alpha/beta (the mineralogical names both compilations mean) keyed by
# formula so I/II on Na2SO4 or C-083 stay i/ii.
JANAF_TRANSITION_ALIASES: dict[tuple[str, Polymorph], Polymorph] = {
    ("SiO2", Polymorph.I): Polymorph.ALPHA,
    ("SiO2", Polymorph.II): Polymorph.BETA,
}

# Printed names / legacy free-form strings → closed token. Not a qualifier-only
# map: these names already identify a mineral or a previously stored token.
POLYMORPH_ALIASES: dict[str, Polymorph] = {
    "alpha": Polymorph.ALPHA,
    "beta": Polymorph.BETA,
    "gamma": Polymorph.GAMMA,
    "delta": Polymorph.DELTA,
    "kappa": Polymorph.KAPPA,
    "i": Polymorph.I,
    "ii": Polymorph.II,
    "iii": Polymorph.III,
    "iv": Polymorph.IV,
    "v": Polymorph.V,
    "alpha_delta": Polymorph.ALPHA_DELTA,
    "alpha-delta": Polymorph.ALPHA_DELTA,
    "beta_rhombohedral": Polymorph.BETA_RHOMBOHEDRAL,
    "beta-rhombohedral": Polymorph.BETA_RHOMBOHEDRAL,
    "quartz": Polymorph.QUARTZ,
    "alpha_quartz": Polymorph.ALPHA_QUARTZ,
    "alpha-quartz": Polymorph.ALPHA_QUARTZ,
    "cristobalite_high": Polymorph.CRISTOBALITE_HIGH,
    "cristobalite, high": Polymorph.CRISTOBALITE_HIGH,
    "cristobalite_low": Polymorph.CRISTOBALITE_LOW,
    "cristobalite, low": Polymorph.CRISTOBALITE_LOW,
    "corundum": Polymorph.CORUNDUM,
    "andalusite": Polymorph.ANDALUSITE,
    "kyanite": Polymorph.KYANITE,
    "sillimanite": Polymorph.SILLIMANITE,
    "mullite": Polymorph.MULLITE,
    "wustite": Polymorph.WUSTITE,
    "pyrrhotite": Polymorph.PYRRHOTITE,
    "troilite": Polymorph.TROILITE,
    "marcasite": Polymorph.MARCASITE,
    "pyrite": Polymorph.PYRITE,
    "hematite": Polymorph.HEMATITE,
    "magnetite": Polymorph.MAGNETITE,
    "anatase": Polymorph.ANATASE,
    "rutile": Polymorph.RUTILE,
    "white": Polymorph.WHITE,
    "red": Polymorph.RED,
    "red_iv": Polymorph.RED_IV,
    "red, iv": Polymorph.RED_IV,
    "red_v": Polymorph.RED_V,
    "red, v": Polymorph.RED_V,
    "black": Polymorph.BLACK,
    "yellow": Polymorph.YELLOW,
    "orthorhombic": Polymorph.ORTHORHOMBIC,
    "monoclinic": Polymorph.MONOCLINIC,
    "monohydrate": Polymorph.MONOHYDRATE,
    "dihydrate": Polymorph.DIHYDRATE,
    "trihydrate": Polymorph.TRIHYDRATE,
    "tetrahydrate": Polymorph.TETRAHYDRATE,
    "hemihexahydrate": Polymorph.HEMIHEXAHYDRATE,
    "diaspore": Polymorph.DIASPORE,
    "boehmite": Polymorph.BOEHMITE,
    "gibbsite": Polymorph.GIBBSITE,
    "kaolinite": Polymorph.KAOLINITE,
    "pyrophyllite": Polymorph.PYROPHYLLITE,
    "anorthite": Polymorph.ANORTHITE,
    "gehlenite": Polymorph.GEHLENITE,
    "grossular": Polymorph.GROSSULAR,
    "grossulsr": Polymorph.GROSSULAR,
    "ca_al_pyroxene": Polymorph.CA_AL_PYROXENE,
    "ca-al pyroxene": Polymorph.CA_AL_PYROXENE,
    "margarite": Polymorph.MARGARITE,
    "prehnite": Polymorph.PREHNITE,
    "zoisite": Polymorph.ZOISITE,
    "wollastonite": Polymorph.WOLLASTONITE,
    "cyclowollastonite": Polymorph.CYCLOWOLLASTONITE,
    "cyclowollastonite (pseudowollastonite)": Polymorph.CYCLOWOLLASTONITE,
    "lime": Polymorph.LIME,
    "dickite": Polymorph.DICKITE,
    "halloysite": Polymorph.HALLOYSITE,
    "larnite": Polymorph.LARNITE,
    "rankinite": Polymorph.RANKINITE,
    "calcium olivine": Polymorph.CALCIUM_OLIVINE,
    "calcium_olivine": Polymorph.CALCIUM_OLIVINE,
    "bcc": Polymorph.BCC,
    "forsterite": Polymorph.FORSTERITE,
    "arsenolite": Polymorph.ARSENOLITE,
    "crystalline": Polymorph.CRYSTALLINE,
    "reference": Polymorph.REFERENCE,
}

CROSS_SOURCE_EQUIVALENCES: tuple[tuple[str, str, str, Polymorph], ...] = (
    ("Al2O3", "Alpha", "Corundum", Polymorph.CORUNDUM),
    ("Al2SiO5", "Andalusite", "Andalusite", Polymorph.ANDALUSITE),
    ("Al2SiO5", "Kyanite", "Kyanite", Polymorph.KYANITE),
    ("Al2SiO5", "Sillimanite", "Sillimanite", Polymorph.SILLIMANITE),
)


def classify_printed_qualifier(qualifier: str | None) -> PrintedQualifierKind:
    if not qualifier:
        return PrintedQualifierKind.NONE
    if qualifier == _ION_QUALIFIER:
        return PrintedQualifierKind.ION
    if qualifier in _ISOMER_QUALIFIERS:
        return PrintedQualifierKind.ISOMER
    if _PRESSURE_QUALIFIER_RE.fullmatch(qualifier):
        return PrintedQualifierKind.PRESSURE
    return PrintedQualifierKind.CRYSTAL


def printed_qualifier_from_name(name: str | None) -> str | None:
    text = str(name or "").strip()
    if ", " not in text:
        return None
    tail = text.split(", ", 1)[1].strip()
    tail = _TRAILING_FORMULA_RE.sub("", tail).strip()
    return tail or None


def printed_qualifier_from_title(title: str | None) -> str | None:
    left = str(title or "").split("|", 1)[0].strip()
    left = _TRAILING_FORMULA_RE.sub("", left).strip()
    return printed_qualifier_from_name(left)


def coerce_polymorph_token(value: object) -> Polymorph:
    if isinstance(value, Polymorph):
        return value
    text = str(value).strip()
    aliased = POLYMORPH_ALIASES.get(text) or POLYMORPH_ALIASES.get(text.lower())
    if aliased is not None:
        return aliased
    return Polymorph(text)


def canonicalize_janaf_transition(formula: str, token: Polymorph) -> Polymorph:
    """Map a JANAF transition-label token through the formula-keyed alias table."""

    return JANAF_TRANSITION_ALIASES.get((formula, token), token)


def lookup_janaf_crystal(formula: str, qualifier: str) -> Polymorph | None:
    token = JANAF_CRYSTAL_DICTIONARY.get((formula, qualifier))
    if token is not None:
        return token
    from simulator.reference_data.janaf import formula_normalised

    return JANAF_CRYSTAL_DICTIONARY.get((formula_normalised(formula), qualifier))


def resolve_janaf_polymorph(
    *,
    formula: str,
    phase_is_crystal: bool,
    name: str,
    title: str,
    transition_token: Polymorph | None = None,
) -> tuple[StateTag, Polymorph | None, str]:
    """Resolve a JANAF polymorph from the fields the table actually prints.

    Returns ``(tag, value, reason)``. Transition-label sides win for a
    segment when present. Otherwise the printed name/title qualifier is
    looked up as (formula, qualifier). Ion / isomer / pressure qualifiers
    are not crystal types.
    """

    if not phase_is_crystal:
        qualifier = printed_qualifier_from_name(name) or printed_qualifier_from_title(
            title
        )
        kind = classify_printed_qualifier(qualifier)
        if kind is PrintedQualifierKind.ION:
            return (
                StateTag.NOT_APPLICABLE,
                None,
                "index_entry.name / title_as_published qualifier "
                f"{qualifier!r} is an ionization state, not a crystal type",
            )
        if kind is PrintedQualifierKind.ISOMER:
            return (
                StateTag.NOT_APPLICABLE,
                None,
                "index_entry.name / title_as_published qualifier "
                f"{qualifier!r} is a molecular isomer, not a crystal type",
            )
        if kind is PrintedQualifierKind.PRESSURE:
            return (
                StateTag.NOT_APPLICABLE,
                None,
                "index_entry.name / title_as_published qualifier "
                f"{qualifier!r} is a pressure qualifier, not a crystal polymorph",
            )
        return StateTag.NOT_APPLICABLE, None, "not crystal"
    if transition_token is not None:
        return StateTag.VALUE, transition_token, ""
    qualifier = printed_qualifier_from_name(name) or printed_qualifier_from_title(title)
    kind = classify_printed_qualifier(qualifier)
    consulted = f"index_entry.name {name!r} and title_as_published {title!r}"
    if kind is PrintedQualifierKind.NONE:
        return (
            StateTag.UNKNOWN,
            None,
            f"{consulted} do not name a crystal polymorph",
        )
    if kind is not PrintedQualifierKind.CRYSTAL:
        return (
            StateTag.UNKNOWN,
            None,
            f"{consulted} qualifier {qualifier!r} is {kind.value}, not a crystal type",
        )
    token = lookup_janaf_crystal(formula, qualifier)
    if token is None:
        return (
            StateTag.UNKNOWN,
            None,
            f"{consulted} qualifier {qualifier!r} is not in the "
            f"(formula, printed qualifier) dictionary for {formula}",
        )
    return StateTag.VALUE, token, ""


def resolve_printed_name_polymorph(name: str | None) -> Polymorph | None:
    """Canonical token for a printed mineral/polymorph name, or None."""

    text = str(name or "").strip()
    if not text:
        return None
    try:
        return coerce_polymorph_token(text)
    except ValueError:
        return None
