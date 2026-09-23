"""Generate schema-v2.1 observations from USGS Bulletin 1544 records."""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from simulator.battery.enums import (
    QUANTITY_UNITS,
    AdmissionStatus,
    EvidenceClass,
    NoticeKind,
    PerBasis,
    Phase,
    Polymorph,
    Quantity,
    UncertaintyKind,
)
from simulator.battery.identity import log10K_from_delta_fG_kJ_mol
from simulator.battery.migrate import dump_yaml, fill_identity, make_species, to_plain
from simulator.battery.polymorph_dictionary import resolve_printed_name_polymorph
from simulator.battery.stable_ids import tabulated_cell_suffix
from simulator.battery.records import (
    Admission,
    Derivation,
    Evidence,
    Located,
    Locator,
    Notice,
    Observation,
    Reaction,
    ReactionTerm,
    Species,
    State,
    Uncertainty,
    Value,
)
from simulator.reference_data.hemingway_haas_robinson_1982_usgs_b1544_loader import (
    COMPILATION_ROOT,
    ROLE,
    SOURCE_ID,
)

RECORDS_DIR = COMPILATION_ROOT / "records"
SOURCE_PATH_PREFIX = (
    "data/literature/compilations/hemingway-haas-robinson-1982-usgs-b1544/records"
)
STANDARD_PRESSURE_PA = Decimal("100000")
B1544_R_J_PER_MOL_K = Decimal("8.3143")
B1544_R_SOURCE = (
    "B1544 does not print a gas constant. R = 8.3143 J/(mol K) is inherited from "
    "the parent compilation USGS B1452 Table 1 (PDF p. 9 / printed p. 3), which "
    "B1544 supplements. Never substitute a modern CODATA constant."
)
UNIT_ROW_QUOTE = "J/mol·K and kJ/mol"
UNIT_ROW_LOCATOR_TEXT = "PDF page 21 (printed p. 15) unit row"
TITLE_PRESSURE_QUOTE = "298.15 K and 1 Bar (10^5 Pascals) Pressure"
CIRCULARITY_WARNING = ROLE["circularity_warning"]
FORMATION_FROM_THE_ELEMENTS = (
    "B1544 heading FORMATION FROM THE ELEMENTS (odd pages; unit row PDF p. 21 / "
    "printed p. 15); schema v2.1 has no closed token for this convention"
)
FORMATION_FROM_THE_OXIDES = (
    "B1544 heading FORMATION FROM THE OXIDES (even continuation pages; asterisks "
    "on ΔfH and ΔfG); schema v2.1 has no closed token for this convention"
)
FORMATION_BASIS_REASON = {
    "from_the_elements": FORMATION_FROM_THE_ELEMENTS,
    "from_the_oxides": FORMATION_FROM_THE_OXIDES,
}
PAGE_UNITS = {
    "temperature": "K",
    "enthalpy_increment_over_T": "J/mol·K",
    "entropy": "J/mol·K",
    "planck_function": "J/mol·K",
    "heat_capacity": "J/mol·K",
    "formation_enthalpy": "kJ/mol",
    "formation_gibbs_energy": "kJ/mol",
    "log_kf": "dimensionless",
    "enthalpy_298_minus_0": "kJ",
    "table1_formation_enthalpy": "kJ/mol at 298.15 K",
}
GRID_VALUE_COLUMNS = (
    "temperature",
    "enthalpy_increment_over_T",
    "entropy",
    "planck_function",
    "heat_capacity",
)
FORMATION_COLUMNS = ("enthalpy", "gibbs_energy", "log_kf")
FORMATION_COLUMN_TO_GRID = {
    "enthalpy": "formation_enthalpy",
    "gibbs_energy": "formation_gibbs_energy",
    "log_kf": "log_kf",
}
STORED_GRID_COLUMNS = frozenset(
    {"entropy", "heat_capacity", "formation_enthalpy", "formation_gibbs_energy", "log_kf"}
)
COLUMN_QUANTITY = {
    "entropy": Quantity.S,
    "heat_capacity": Quantity.CP,
    "formation_enthalpy": Quantity.DELTA_FH,
    "formation_gibbs_energy": Quantity.DELTA_FG,
    "log_kf": Quantity.LOG10_KF,
    "table1_formation_enthalpy": Quantity.DELTA_FH,
    "helgeson_corrected": Quantity.DELTA_FH,
}
UNCERTAINTY_EXCLUDE_REASON = (
    "printed uncertainty; attached to stored sibling observations of the matching "
    "column (TABLE 1 per literature source; T-grid table-level per column and "
    "formation basis). Not itself a stored quantity."
)
VOCABULARY_GAP_COLUMNS = {
    "enthalpy_increment_over_T": (
        "printed (H°T−H°298)/T in J/mol·K; schema v2.1 names H_minus_H298 as "
        "kJ/mol of H(T)−H(298), not the printed /T function. Not transformed."
    ),
    "planck_function": (
        "printed −(G°T−H°298)/T is the Gibbs-function transcription check "
        "−(G−H298)/T = S − (H−H298)/T; not stored"
    ),
    "enthalpy_298_minus_0": (
        "printed H°298−H°0 footer in kJ; schema v2.1 has no H298−H0 quantity"
    ),
}
NAMED_DAMAGED_TOKENS = (
    {
        "as_published": "107.2J",
        "record_id": "usgs-b1544-sillimanite",
        "reason": (
            "named OCR letter tail '107.2J'; letter tail is real but 107.2 as "
            "Cp does not fit the Sillimanite series (neighbours ~197). Kept refused."
        ),
    },
    {
        "as_published": "-1625.31P",
        "record_id": "usgs-b1544-casio3-reference",
        "reason": (
            "named OCR letter tail '-1625.31P'; remaining digits grain 0.01 is "
            "coarser than the CaSiO3 from-the-elements formation-enthalpy column "
            "grain 0.001 (17 clean values, e.g. -1627.150 / -1623.436). P is a "
            "mangled third decimal, not a letter to drop. Page image (PDF p.55 / "
            "printed p.49, 1500 K) does not corroborate the missing digit. Refused."
        ),
    },
)
# Reconstruct only where the printed page or an arithmetic identity
# corroborates. Raw as_published is kept. 107.2J and -1625.31P are the
# discriminating letter-tail controls that are not repaired.
RECONSTRUCTED_TOKENS = (
    {
        "as_published": ":198.15",
        "record_id": "usgs-b1544-kaolinite",
        "value": Decimal("298.15"),
        "method_class": "reconstructed_from_heading_and_logkf_identity",
        "evidence": (
            "Kaolinite p.59 first row under heading 'Crystals 298.15 to 1000 K'; "
            "(H−H298)/T=0.000; log Kf 665.679 matches ΔfG −3799.611 at 298.15 "
            "with R=8.3143 (predicted 665.679); the 335.94 identity delta is the "
            "T error of reading :198.15 as 198.15. Reconstruct 298.15."
        ),
    },
    {
        "as_published": "374.e9",
        "record_id": "usgs-b1544-rankinite",
        "value": Decimal("374.883"),
        "method_class": "reconstructed_from_gibbs_function_identity",
        "evidence": (
            "Rankinite 1200 K: S − (H−H298)/T = 582.49 − 207.607 = 374.883; "
            "OCR e→8. Reconstruct 374.883."
        ),
    },
    {
        "as_published": "13!l.799",
        "record_id": "usgs-b1544-rankinite",
        "value": Decimal("138.799"),
        "method_class": "reconstructed_from_logkf_identity",
        "evidence": (
            "Rankinite 1200 K ΔfG −3188.650 kJ/mol → log Kf = "
            "−1000×(−3188.650)/(8.3143×1200×ln 10) ≈ 138.80; OCR !l→8. "
            "Reconstruct 138.799."
        ),
    },
    {
        "as_published": ":..111.855",
        "record_id": "usgs-b1544-ca3sio5-reference",
        "value": Decimal("-111.855"),
        "method_class": "reconstructed_from_neighbour_sign",
        "evidence": (
            "Ca3SiO5-reference oxides ΔfH at 1300 K between −113.073 and "
            "−110.445 under heading FORMATION FROM THE OXIDES; insert minus. "
            "Reconstruct −111.855."
        ),
    },
    {
        "as_published": "199.3R",
        "record_id": "usgs-b1544-sillimanite",
        "value": Decimal("199.38"),
        "method_class": "reconstructed_from_printed_page",
        "evidence": (
            "Sillimanite PDF p.35 / printed p.29, 1200 K Cp. The page image prints "
            "199.38 (between 1100=197.21 and 1300=201.07). Column printed grain is "
            "0.01 (14 of 14 clean Cp values carry two decimals). Remaining digits "
            "after dropping R are 199.3 (grain 0.1), which is coarser than the "
            "column, so R is a mangled 8 not a letter tail. Reconstruct 199.38."
        ),
    },
)
LETTER_TAIL_RE = re.compile(r".*\d[A-Za-z]$")
_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?$")
_EXTRACT_NUMBER_RE = re.compile(
    r"[+-]?\d+(?:\.\d*)?(?:[Ee][+-]?\d+)?"
)
_LETTER_RE = re.compile(r"[A-DF-Za-df-z]")
PHASE_SPLITS = {
    "usgs-b1544-h2o-reference": {
        "split_row": 2,
        "low": (Phase.L, None),
        "high": (Phase.G, None),
    },
    "usgs-b1544-quartz": {
        "split_row": 7,
        "low": (Phase.CR, "alpha"),
        "high": (Phase.CR, "beta"),
    },
    "usgs-b1544-alooh-reference": {
        "split_row": 4,
        "low": (Phase.CR, "diaspore"),
        "high": (Phase.CR, "boehmite"),
    },
    # PDF p.55 / printed p.49: Wollastonite crystals 298.15 to 1398 K;
    # Cyclowollastonite stable above 1398 K. Two printed rows at 1398 K
    # (S=255.15 then S=259.29). split_row is the high-side row index.
    "usgs-b1544-casio3-reference": {
        "split_row": 12,
        "low": (Phase.CR, "wollastonite"),
        "high": (Phase.CR, "cyclowollastonite"),
    },
}
MISSING_FORMATION_BASIS_REASON = (
    "source does not state formation basis; unknown is not a default"
)
# B1544 title-page system is Al2O3–CaO–SiO2–H2O. Formation-from-the-oxides is
# from those four oxides; formation-from-the-elements is from Al(cr), Ca(cr),
# Si(cr), O2(g), H2(g). The page prints the heading, not a stoichiometric
# equation; the compared identity value is that convention as a balanced
# Reaction so identity_equal can distinguish the bases.
_PAREN_GROUP_RE = re.compile(r"\(([A-Za-z0-9]+)\)(\d*)")
_FORMULA_TOKEN_RE = re.compile(r"(Al|Ca|Si|O|H)(\d+)?")
_REFERENCE_NAME_SUFFIXES = (" - Reference", " Reference", " - reference", " reference")


def _crystal(formula: str, polymorph: str) -> Species:
    token = resolve_printed_name_polymorph(polymorph)
    if token is None:
        raise ValueError(f"unrecognised B1544 polymorph {polymorph!r}")
    return Species(formula, Phase.CR, polymorph=State.of(token), charge=0)


def _gas(formula: str) -> Species:
    return Species(
        formula, Phase.G, polymorph=State.not_applicable("not crystal"), charge=0
    )


def _liquid(formula: str) -> Species:
    return Species(
        formula, Phase.L, polymorph=State.not_applicable("not crystal"), charge=0
    )


def _polymorph_from_printed_name(name: str, missing_reason: str) -> State[Polymorph]:
    token = resolve_printed_name_polymorph(name)
    if token is None:
        return State.unknown(missing_reason)
    return State.of(token)


# Printed under TRANSITIONS IN REFERENCE STATE ELEMENTS, e.g. Sillimanite
# PDF p.35 / printed p.29 (Al M.P. 933 K, Si M.P. 1685 K) and CaSiO3
# PDF p.55 / printed p.49 (Ca ALPHA-BETA 737, M.P. BETA 1123, B.P. 1755 K).
# Applied compilation-wide: some JSON records have empty notes while the
# page still prints the block (Sillimanite, Kyanite). At and above a
# transition temperature the high-T phase is the reference; B1544 prints
# the formation jump after a dashed line on the high-T side of T_trs.
_AL_MELTING_K = Decimal("933")
_SI_MELTING_K = Decimal("1685")
_CA_ALPHA_BETA_K = Decimal("737")
_CA_MELTING_K = Decimal("1123")
_CA_BOILING_K = Decimal("1755")
_REFERENCE_TEMPERATURE_DEFAULT_K = Decimal("298.15")


def _element_reference_species(
    symbol: str, temperature: Decimal | None
) -> Species:
    """Elemental reference species at T. O and H are stored as O2(g) and H2(g)."""

    t = _REFERENCE_TEMPERATURE_DEFAULT_K if temperature is None else temperature
    if symbol == "O":
        return _gas("O2")
    if symbol == "H":
        return _gas("H2")
    if symbol == "Al":
        if t >= _AL_MELTING_K:
            return _liquid("Al")
        return _crystal("Al", "reference")
    if symbol == "Si":
        if t >= _SI_MELTING_K:
            return _liquid("Si")
        return _crystal("Si", "reference")
    if symbol == "Ca":
        if t >= _CA_BOILING_K:
            return _gas("Ca")
        if t >= _CA_MELTING_K:
            return _liquid("Ca")
        if t >= _CA_ALPHA_BETA_K:
            return _crystal("Ca", "beta")
        return _crystal("Ca", "alpha")
    raise ValueError(f"unrecognised B1544 reference element {symbol!r}")
# Cation → (oxide formula, oxide species, cation atoms per oxide formula).
_OXIDE_SPECIES = {
    "Al": ("Al2O3", _crystal("Al2O3", "corundum"), Fraction(2)),
    "Ca": ("CaO", _crystal("CaO", "lime"), Fraction(1)),
    "Si": ("SiO2", _crystal("SiO2", "quartz"), Fraction(1)),
    "H": ("H2O", _liquid("H2O"), Fraction(2)),
}


def _expand_formula_groups(formula: str) -> str:
    text = formula
    while True:
        match = _PAREN_GROUP_RE.search(text)
        if match is None:
            break
        inner = match.group(1)
        count = int(match.group(2) or "1")
        text = text[: match.start()] + inner * count + text[match.end() :]
    if "(" in text or ")" in text:
        raise ValueError(f"unexpanded parentheses in formula {formula!r}")
    return text


def _element_counts(formula: str) -> dict[str, Fraction]:
    """Parse a B1544 formula in Al–Ca–Si–O–H, including (OH) groups."""

    expanded = _expand_formula_groups(formula)
    counts: dict[str, Fraction] = {}
    position = 0
    for match in _FORMULA_TOKEN_RE.finditer(expanded):
        if match.start() != position:
            raise ValueError(
                f"unparsed formula {formula!r} at {expanded[position:]!r}"
            )
        position = match.end()
        element = match.group(1)
        n = match.group(2)
        counts[element] = counts.get(element, Fraction(0)) + Fraction(int(n) if n else 1)
    if position != len(expanded):
        raise ValueError(f"unparsed formula {formula!r} at {expanded[position:]!r}")
    return counts


def _formation_identity(
    product: Species, basis: str, temperature: Decimal | None = None
) -> tuple[Reaction, tuple[tuple[str, Species], ...]]:
    """Compared reaction + formation_elements for a printed B1544 heading.

    Algebra, kyanite Al2SiO5:
    from the elements: Al2SiO5 = 2 Al + Si + 5/2 O2
    from the oxides:   Al2SiO5 = Al2O3 + SiO2
    Oxide oxygen check: 1·Al2O3 + 1·SiO2 contributes 3+2 = 5 O; leftover O = 0.
    Elemental reference phases follow the printed TRANSITIONS IN REFERENCE
    STATE ELEMENTS block at `temperature`.
    """

    counts = _element_counts(product.formula)
    elements = tuple(
        (element, _element_reference_species(element, temperature))
        for element in ("Al", "Ca", "Si", "O", "H")
        if element in counts
    )
    terms = [ReactionTerm(product, Fraction(1))]
    if basis == "from_the_elements":
        for element in ("Al", "Ca", "Si"):
            if element in counts:
                terms.append(
                    ReactionTerm(
                        _element_reference_species(element, temperature),
                        -counts[element],
                    )
                )
        if "O" in counts:
            terms.append(
                ReactionTerm(
                    _element_reference_species("O", temperature), -counts["O"] / 2
                )
            )
        if "H" in counts:
            terms.append(
                ReactionTerm(
                    _element_reference_species("H", temperature), -counts["H"] / 2
                )
            )
    elif basis == "from_the_oxides":
        oxygen_from_oxides = Fraction(0)
        for element in ("Al", "Ca", "Si", "H"):
            if element not in counts:
                continue
            oxide_formula, oxide_species, cations_per = _OXIDE_SPECIES[element]
            n_oxide = counts[element] / cations_per
            terms.append(ReactionTerm(oxide_species, -n_oxide))
            oxygen_from_oxides += n_oxide * _element_counts(oxide_formula).get(
                "O", Fraction(0)
            )
        leftover_o = counts.get("O", Fraction(0)) - oxygen_from_oxides
        if leftover_o != 0:
            raise ValueError(
                f"{product.formula}: oxide-system oxygen leftover {leftover_o}; "
                "B1544 minerals must close in Al2O3–CaO–SiO2–H2O"
            )
    else:
        raise ValueError(f"unrecognised formation basis {basis!r}")
    return Reaction(tuple(terms)), elements


def _basis_states(
    product: Species, basis: str | None, temperature: Decimal | None = None
) -> tuple[State[Reaction], State[tuple[tuple[str, Species], ...]]]:
    """Resolved compared values, or unknown when the basis key is missing.

    A missing basis is unknown. It is not inferred as from-the-elements.
    """

    if basis not in FORMATION_BASIS_REASON:
        return (
            State.unknown(MISSING_FORMATION_BASIS_REASON),
            State.unknown(MISSING_FORMATION_BASIS_REASON),
        )
    reaction, elements = _formation_identity(product, basis, temperature)
    return State.of(reaction), State.of(elements)


def _record_mineral_name(record: Mapping[str, Any]) -> str:
    name = str(record.get("name_as_published") or "").strip()
    lowered = name.lower()
    for suffix in _REFERENCE_NAME_SUFFIXES:
        if lowered.endswith(suffix.lower()):
            name = name[: -len(suffix)].strip()
            break
    return name


def _name_is_formula_like(name: str, formula: str) -> bool:
    """A formula-like table title is not a resolved polymorph."""

    def norm(text: str) -> str:
        return "".join(character for character in text.lower() if character.isalnum())

    return bool(name) and bool(formula) and norm(name) == norm(formula)


def _table_has_phase_change_rows(record: Mapping[str, Any]) -> bool:
    """True when two T-grid rows share a printed temperature (a phase split)."""

    temperatures: list[str] = []
    for row in record.get("rows") or ():
        if not isinstance(row, Mapping) or "temperature" not in row:
            continue
        parsed = _as_published_cell(row.get("temperature"))
        if parsed is None:
            continue
        temperatures.append(parsed[0])
    return len(temperatures) != len(set(temperatures))


def _table1_mineral_name(phase_as_published: str) -> str:
    parts = phase_as_published.strip().split()
    if len(parts) < 2:
        return ""
    return " ".join(parts[:-1])


@dataclass(frozen=True)
class RecordGeneration:
    observations: tuple[Observation, ...]
    report: Mapping[str, Any]


@dataclass(frozen=True)
class RawToken:
    record_id: str
    path: str
    as_published: str
    ocr_suspect: bool
    flags: tuple[str, ...]
    column: str
    formation_basis: str | None
    row_index: int | None
    table1_source: str | None


def _as_published_cell(cell: object) -> tuple[str, bool, tuple[str, ...]] | None:
    if not isinstance(cell, Mapping) or "as_published" not in cell:
        return None
    token = cell.get("as_published")
    if token is None:
        return None
    flags = tuple(str(flag) for flag in (cell.get("flags") or ()))
    return str(token), bool(cell.get("ocr_suspect")), flags


def _iter_raw_numeric_tokens(record: Mapping[str, Any]) -> list[RawToken]:
    """Count printed numeric cells from as_published text only — never cell['value']."""

    record_id = str(record.get("record_id") or "")
    tokens: list[RawToken] = []

    def add(
        path: str,
        cell: object,
        column: str,
        *,
        basis: str | None = None,
        row_index: int | None = None,
        table1_source: str | None = None,
    ) -> None:
        parsed = _as_published_cell(cell)
        if parsed is None:
            return
        token, suspect, flags = parsed
        tokens.append(
            RawToken(
                record_id=record_id,
                path=path,
                as_published=token,
                ocr_suspect=suspect,
                flags=flags,
                column=column,
                formation_basis=basis,
                row_index=row_index,
                table1_source=table1_source,
            )
        )

    footer = record.get("enthalpy_298_minus_0")
    add("enthalpy_298_minus_0", footer, "enthalpy_298_minus_0")
    uncertainty = record.get("uncertainty")
    if isinstance(uncertainty, Mapping):
        for basis, block in uncertainty.items():
            if not isinstance(block, Mapping):
                continue
            for column, cell in block.items():
                add(
                    f"uncertainty.{basis}.{column}",
                    cell,
                    f"uncertainty_{column}",
                    basis=str(basis),
                )
    rows = record.get("rows") or []
    if not isinstance(rows, list):
        raise ValueError(f"{record_id}: rows must be a list")
    for row_index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"{record_id}: rows[{row_index}] is not a mapping")
        if "values" in row:
            values = row.get("values") or {}
            if isinstance(values, Mapping):
                for source, cell in values.items():
                    add(
                        f"rows[{row_index}].values.{source}",
                        cell,
                        "table1_formation_enthalpy",
                        basis="from_the_elements",
                        row_index=row_index,
                        table1_source=str(source),
                    )
            uncertainties = row.get("uncertainties") or {}
            if isinstance(uncertainties, Mapping):
                for source, cell in uncertainties.items():
                    add(
                        f"rows[{row_index}].uncertainties.{source}",
                        cell,
                        "table1_uncertainty",
                        basis="from_the_elements",
                        row_index=row_index,
                        table1_source=str(source),
                    )
            add(
                f"rows[{row_index}].helgeson_corrected",
                row.get("helgeson_corrected"),
                "helgeson_corrected",
                basis="from_the_elements",
                row_index=row_index,
                table1_source="helgeson_1978",
            )
            continue
        for column in GRID_VALUE_COLUMNS:
            add(
                f"rows[{row_index}].{column}",
                row.get(column),
                column,
                row_index=row_index,
            )
        formation = row.get("formation") or {}
        if isinstance(formation, Mapping):
            for basis, block in formation.items():
                if not isinstance(block, Mapping):
                    continue
                for field in FORMATION_COLUMNS:
                    add(
                        f"rows[{row_index}].formation.{basis}.{field}",
                        block.get(field),
                        FORMATION_COLUMN_TO_GRID[field],
                        basis=str(basis),
                        row_index=row_index,
                    )
    return tokens


def _published_decimal(token: str) -> Decimal | None:
    text = token.strip()
    if text.startswith("(") and text.endswith(")") and text != "()":
        text = text[1:-1].strip()
    if not text or not _NUMBER_RE.fullmatch(text):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _decimal_grain(token: str) -> Decimal:
    """10^exponent of a printed numeric token. Same concept as janaf.py."""

    value = Decimal(token.strip())
    return Decimal(1).scaleb(value.as_tuple().exponent)


def _value_grain(value: Decimal) -> Decimal:
    """10^exponent of a Decimal. Same scale as `_decimal_grain` / janaf.py."""

    return Decimal(1).scaleb(value.as_tuple().exponent)


def _column_grain_map(
    record: Mapping[str, Any],
) -> dict[tuple[str, str | None], Decimal]:
    """Modal printed decimal grain per (column, formation_basis).

    Grain is the mode of `_decimal_grain(as_published)` among clean cells in
    that column: parseable, not OCR-suspect unless image-verified, not a named
    damaged token, not a reconstruction candidate. A reconstruction must not
    be coarser than this grain (larger 10^exponent means fewer printed
    decimals). Dropping a trailing letter is only legitimate when the remaining
    digits already match the grain; otherwise the tail was a digit.
    """

    buckets: dict[tuple[str, str | None], list[Decimal]] = defaultdict(list)
    for token in _iter_raw_numeric_tokens(record):
        if _reconstruction_for(token.as_published, token.record_id) is not None:
            continue
        if _named_damage_reason(token):
            continue
        if token.ocr_suspect and not _image_verified(token):
            continue
        value = _published_decimal(token.as_published)
        if value is None:
            continue
        buckets[(token.column, token.formation_basis)].append(_value_grain(value))
    return {
        key: Counter(values).most_common(1)[0][0]
        for key, values in buckets.items()
        if values
    }


def _reconstruction_coarser_than_column(
    value: Decimal, token: RawToken, grains: Mapping[tuple[str, str | None], Decimal]
) -> str | None:
    grain = grains.get((token.column, token.formation_basis))
    if grain is None:
        return None
    value_grain = _value_grain(value)
    if value_grain <= grain:
        return None
    return (
        f"reconstruction grain {value_grain} is coarser than column printed "
        f"grain {grain} for {token.column}"
        + (f" / {token.formation_basis}" if token.formation_basis else "")
        + f"; as_published={token.as_published!r} reconstructed={value}"
    )


def _letter_tail_grain_reason(
    token: RawToken, grains: Mapping[tuple[str, str | None], Decimal]
) -> str | None:
    text = token.as_published.strip()
    if not LETTER_TAIL_RE.fullmatch(text):
        return None
    remaining = text[:-1]
    if _published_decimal(remaining) is None:
        return None
    grain = grains.get((token.column, token.formation_basis))
    if grain is None:
        return None
    remaining_grain = _decimal_grain(remaining)
    if remaining_grain == grain:
        return None
    return (
        f"OCR letter tail {token.as_published!r}: remaining digits grain "
        f"{remaining_grain} does not match column printed grain {grain}; "
        "tail is a digit, not dropped"
    )


def _named_damage_reason(token: RawToken) -> str | None:
    for item in NAMED_DAMAGED_TOKENS:
        if (
            item["as_published"] == token.as_published
            and item["record_id"] == token.record_id
        ):
            return str(item["reason"])
    if LETTER_TAIL_RE.fullmatch(token.as_published.strip()):
        return f"OCR letter tail {token.as_published!r}"
    return None


def _reconstruction(token: RawToken) -> dict[str, Any] | None:
    return _reconstruction_for(token.as_published, token.record_id)


def _usable_reconstruction(
    token: RawToken | None,
    grains: Mapping[tuple[str, str | None], Decimal],
) -> dict[str, Any] | None:
    if token is None:
        return None
    item = _reconstruction(token)
    if item is None:
        return None
    value = _reconstruction_decimal(item)
    if _reconstruction_coarser_than_column(value, token, grains) is not None:
        return None
    return item


def _token_lookup(
    tokens: Sequence[RawToken],
    row_index: int,
    column: str,
    basis: str | None,
) -> RawToken | None:
    return next(
        (
            token
            for token in tokens
            if token.row_index == row_index
            and token.column == column
            and token.formation_basis == basis
        ),
        None,
    )


def _reconstruction_for(as_published: str, record_id: str) -> dict[str, Any] | None:
    for item in RECONSTRUCTED_TOKENS:
        if item["as_published"] == as_published and item["record_id"] == record_id:
            return item
    return None


def _reconstruction_decimal(item: Mapping[str, Any]) -> Decimal:
    value = item["value"]
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _reconstructed_value(
    as_published: str,
    record_id: str,
    *,
    token: RawToken | None = None,
    grains: Mapping[tuple[str, str | None], Decimal] | None = None,
) -> Decimal | None:
    item = _reconstruction_for(as_published, record_id)
    if item is None:
        return None
    value = _reconstruction_decimal(item)
    if token is not None and grains is not None:
        if _reconstruction_coarser_than_column(value, token, grains) is not None:
            return None
    return value


def _identity_token_text(
    cell: tuple[str, bool, tuple[str, ...]] | None,
    record_id: str,
    *,
    token: RawToken | None = None,
    grains: Mapping[tuple[str, str | None], Decimal] | None = None,
) -> str | None:
    """Numeric string for an identity check: reconstructed value, or clean as_published."""

    if cell is None:
        return None
    text, suspect, _flags = cell
    reconstructed = _reconstructed_value(text, record_id, token=token, grains=grains)
    if reconstructed is not None:
        return format(reconstructed, "f")
    if suspect:
        return None
    if _published_decimal(text) is None:
        return None
    return text


def _image_verified(token: RawToken) -> bool:
    return "image_verified_correction" in token.flags


def _unit_row_locator() -> Locator:
    return Locator(
        pdf_page_index=21,
        published_page=15,
        note=f"{UNIT_ROW_LOCATOR_TEXT}: {UNIT_ROW_QUOTE}",
    )


def _page_for(
    record: Mapping[str, Any], basis: str | None
) -> tuple[int | None, int | None]:
    printed = list(record.get("printed_pages") or ())
    pdf = list(record.get("pdf_pages") or ())
    if basis == "from_the_oxides" and len(printed) > 1:
        return printed[1], pdf[1] if len(pdf) > 1 else (pdf[0] if pdf else None)
    return (printed[0] if printed else None), (pdf[0] if pdf else None)


def _phase_state(
    record: Mapping[str, Any], row_index: int | None, table1_phase: str | None
) -> tuple[State[Phase], State[Polymorph]]:
    record_id = str(record.get("record_id") or "")
    if record_id == "usgs-b1544-table-1":
        label = (table1_phase or "").lower()
        if "water" in label:
            return State.of(Phase.L), State.not_applicable("not crystal")
        name = _table1_mineral_name(table1_phase or "")
        if name:
            return (
                State.of(Phase.CR),
                _polymorph_from_printed_name(
                    name,
                    f"name_as_published / TABLE 1 phase_as_published {table1_phase!r} "
                    "is not a closed Polymorph token",
                ),
            )
        return (
            State.of(Phase.CR),
            State.unknown("TABLE 1 row is missing the mineral name"),
        )
    split = PHASE_SPLITS.get(record_id)
    if split is not None and row_index is not None:
        side = split["high"] if row_index >= int(split["split_row"]) else split["low"]
        phase, polymorph = side
        polymorph_state = (
            State.not_applicable("not crystal")
            if phase is not Phase.CR
            else (
                _polymorph_from_printed_name(
                    str(polymorph),
                    "B1544 phase line does not name a polymorph token",
                )
                if polymorph
                else State.unknown("B1544 phase line does not name a polymorph token")
            )
        )
        return State.of(phase), polymorph_state
    text = (record.get("phase_as_published") or "").lower()
    if "ideal gas" in text and "crystal" not in text and "liquid" not in text:
        return State.of(Phase.G), State.not_applicable("not crystal")
    if "liquid" in text and "crystal" not in text:
        return State.of(Phase.L), State.not_applicable("not crystal")
    if _table_has_phase_change_rows(record):
        return (
            State.of(Phase.CR),
            State.unknown(
                "B1544 table documents more than one product phase; "
                "name_as_published is not a resolved polymorph"
            ),
        )
    name = _record_mineral_name(record)
    formula = str(record.get("formula_as_published") or "")
    if name and not _name_is_formula_like(name, formula):
        return (
            State.of(Phase.CR),
            _polymorph_from_printed_name(
                name,
                f"name_as_published {name!r} is not a closed Polymorph token",
            ),
        )
    return (
        State.of(Phase.CR),
        State.unknown(
            "name_as_published and phase_as_published name crystals but not a "
            "polymorph token"
        ),
    )


def _table1_formula(phase_as_published: str) -> str:
    parts = phase_as_published.strip().split()
    if not parts:
        raise ValueError("TABLE 1 row is missing phase_as_published")
    return parts[-1]


def _formula(record: Mapping[str, Any], table1_phase: str | None) -> str:
    if str(record.get("record_id") or "") == "usgs-b1544-table-1":
        return _table1_formula(table1_phase or "")
    formula = str(record.get("formula_as_published") or "")
    if not formula:
        raise ValueError(f"{record.get('record_id')}: missing formula_as_published")
    return formula


def _table1_attribution(record: Mapping[str, Any], source: str | None) -> str:
    columns = list(record.get("columns_as_published") or ())
    mapping = {
        "haas_1979": 1,
        "robie_1979": 2,
        "helgeson_1978": 3,
        "hemley_1980": 4,
    }
    if source == "helgeson_1978" or source is None:
        pass
    index = mapping.get(source or "", None)
    if index is not None and index < len(columns):
        return str(columns[index])
    return source or "TABLE 1 literature column"


def _extract_signed_number(token: str) -> Decimal | None:
    if _LETTER_RE.search(token):
        return None
    matches = list(_EXTRACT_NUMBER_RE.finditer(token))
    if len(matches) != 1:
        return None
    try:
        return Decimal(matches[0].group())
    except InvalidOperation:
        return None


def _gibbs_identity(
    entropy_token: str, hht_token: str, gef_token: str
) -> tuple[Decimal, Decimal, Decimal]:
    entropy = Decimal(entropy_token)
    hht = Decimal(hht_token)
    gef = Decimal(gef_token)
    calculated = entropy - hht
    tolerance = (
        _decimal_grain(entropy_token) / 2
        + _decimal_grain(hht_token) / 2
        + _decimal_grain(gef_token) / 2
    )
    residual = abs(gef - calculated)
    return residual, tolerance, calculated


def _logk_identity(
    temperature_token: str, gibbs_token: str, logk_token: str
) -> tuple[Decimal, Decimal, Decimal] | None:
    temperature = Decimal(temperature_token)
    if temperature <= 0:
        return None
    gibbs = Decimal(gibbs_token)
    logk = Decimal(logk_token)
    # Premise: B1544 is silent on R; use B1452 Table 1 R = 8.3143 J/(mol K).
    # Algebra: log10 Kf = -1000 * delta_fG / (R T ln 10) with delta_fG in kJ/mol.
    # Unit check: (kJ/mol)*1000 J/kJ / ((J/(mol K))*K) is dimensionless.
    # Worked row: Corundum 298.15 K, delta_fG = -1582.242 kJ/mol → 277.2022… vs printed 277.203.
    calculated = log10K_from_delta_fG_kJ_mol(
        gibbs,
        temperature,
        gas_constant_J_per_mol_K=B1544_R_J_PER_MOL_K,
    )
    e_t = _decimal_grain(temperature_token) / 2
    if temperature - e_t <= 0:
        return None
    denominator = abs(
        log10K_from_delta_fG_kJ_mol(
            Decimal("1"),
            temperature - e_t,
            gas_constant_J_per_mol_K=B1544_R_J_PER_MOL_K,
        )
    )
    temperature_term = abs(calculated) * e_t / (temperature - e_t)
    tolerance = (
        _decimal_grain(logk_token) / 2
        + _decimal_grain(gibbs_token) / 2 * denominator
        + temperature_term
    )
    residual = abs(logk - calculated)
    return residual, tolerance, calculated


def _identity_notice(
    *,
    quantity: Quantity,
    residual: Decimal,
    tolerance: Decimal,
    identity_name: str,
    basis: str | None,
    temperature: str,
) -> Notice:
    return Notice(
        kind=NoticeKind.OUT_OF_CERTIFIED_BAND,
        affected_quantities=(quantity,),
        reason=(
            f"identity {identity_name} residual={residual} exceeds 1× printed-rounding "
            f"tolerance {tolerance} but not 10×; stored as published"
            + (f"; formation_basis={basis}" if basis else "")
            + f"; T={temperature}"
        ),
        origin="usgs-b1544-generator",
        original=residual,
        band="printed last-place rounding (1× < residual ≤ 10×)",
    )


def _observation(
    *,
    record: Mapping[str, Any],
    token: RawToken,
    quantity: Quantity,
    value: Decimal,
    temperature: Decimal,
    notices: tuple[Notice, ...],
    table1_phase: str | None,
    uncertainty: Uncertainty | None = None,
    reconstruction: Mapping[str, Any] | None = None,
    temperature_reconstruction: Mapping[str, Any] | None = None,
) -> Observation:
    record_id = str(record["record_id"])
    formula = _formula(record, table1_phase)
    phase, polymorph = _phase_state(record, token.row_index, table1_phase)
    species = make_species(formula, phase, polymorph)
    basis = token.formation_basis
    basis_reason = FORMATION_BASIS_REASON.get(basis or "", "")
    known: dict[str, Any] = {
        "per": State.of(PerBasis.MOL_SPECIES),
        "temperature_K": State.of(temperature),
        "standard_pressure_Pa": State.of(STANDARD_PRESSURE_PA),
    }
    if quantity in {Quantity.DELTA_FH, Quantity.DELTA_FG, Quantity.LOG10_KF}:
        known["reaction"], known["formation_elements"] = _basis_states(
            species, basis, temperature
        )
    identity = fill_identity(quantity, species, **known)
    published_page, pdf_page = _page_for(record, basis)
    unit_key = token.column if token.column in PAGE_UNITS else "formation_enthalpy"
    unit = PAGE_UNITS[unit_key]
    source_path = f"{SOURCE_PATH_PREFIX}/{record_id}.json"
    note = (
        f"as_published={token.as_published!r}; unit={unit!r} quoted from "
        f"{UNIT_ROW_LOCATOR_TEXT if record_id != 'usgs-b1544-table-1' else 'TABLE 1 header PDF p. 15 / printed p. 9'}"
        f"; standard_pressure=1 bar from title ({TITLE_PRESSURE_QUOTE})"
    )
    if basis:
        note += f"; formation_basis={basis}"
    if token.table1_source:
        note += f"; literature_source={token.table1_source}"
    if reconstruction is not None:
        note += (
            f"; reconstructed={reconstruction['value']!s} "
            f"method_class={reconstruction['method_class']}; "
            f"evidence={reconstruction['evidence']}"
        )
    if temperature_reconstruction is not None:
        note += (
            f"; temperature_reconstructed={temperature_reconstruction['value']!s} "
            f"method_class={temperature_reconstruction['method_class']}; "
            f"evidence={temperature_reconstruction['evidence']}"
        )
    locator = Locator(
        published_page=published_page,
        pdf_page_index=pdf_page,
        table=record_id if record_id != "usgs-b1544-table-1" else "TABLE 1",
        source_path=source_path,
        record=record_id,
        note=note,
    )
    role_note = (
        "compilation_role engine_reference_input=true, scoring_eligible=false; "
        f"circularity_warning={CIRCULARITY_WARNING}"
    )
    if record_id == "usgs-b1544-table-1":
        attribution = _table1_attribution(record, token.table1_source)
        if token.column == "helgeson_corrected":
            attribution = str(record.get("footnote_as_published") or attribution)
            relation = (
                "TABLE 1 parenthetical Helgeson-corrected ΔfH at 298.15 K; "
                f"quoted from {attribution}; {role_note}"
            )
            evidence = Evidence(
                class_=State.of(EvidenceClass.QUOTED_ATTRIBUTED),
                original_method_class="quoted_literature_enthalpy_of_formation",
                attribution=attribution,
            )
        else:
            relation = (
                "TABLE 1 ΔfH at 298.15 kJ/mol belongs to the cited literature column "
                f"{attribution!r}, not to B1544's own T-grid assessment; {role_note}"
            )
            evidence = Evidence(
                class_=State.of(EvidenceClass.QUOTED_ATTRIBUTED),
                original_method_class="quoted_literature_enthalpy_of_formation",
                attribution=attribution,
            )
    else:
        relation = f"Direct B1544 T-grid tabulation; {role_note}"
        if basis_reason:
            relation += f"; formation_basis={basis_reason}"
        method_class = "assessed_thermodynamic_functions"
        if reconstruction is not None:
            method_class = str(reconstruction["method_class"])
            relation += f"; reconstructed from {token.as_published!r} via {method_class}"
        elif temperature_reconstruction is not None:
            method_class = str(temperature_reconstruction["method_class"])
            relation += (
                f"; row temperature reconstructed from "
                f"{temperature_reconstruction['as_published']!r} via {method_class}"
            )
        evidence = Evidence(
            class_=State.of(EvidenceClass.COMPILATION_ASSESSED),
            original_method_class=method_class,
            model="assessed_thermodynamic_functions",
        )
    parameters: list[tuple[str, Located[Decimal]]] = []
    if quantity in {Quantity.DELTA_FH, Quantity.DELTA_FG}:
        parameters.append(
            (
                "kj_to_j",
                Located(State.of(Decimal("1000")), locator=_unit_row_locator()),
            )
        )
    published_name = (
        str(record.get("name_as_published") or table1_phase or "") or None
    )
    suffix = tabulated_cell_suffix(
        quantity.value,
        temperature=temperature,
        column=token.column,
        basis=basis or "shared",
        name=published_name,
        extra=f"src={token.table1_source or 'grid'}",
    )
    return Observation(
        observation_id=f"{SOURCE_ID}:{record_id}:{suffix}",
        experiment_id=f"{SOURCE_ID}:{record_id}:tabulation",
        identity=identity,
        value=Value.point_of(value),
        uncertainty=uncertainty or Uncertainty(kind=UncertaintyKind.NONE),
        evidence=evidence,
        admission=Admission(
            status=AdmissionStatus.PENDING,
            reason="source does not state admission_status",
        ),
        notices=notices,
        source_id=SOURCE_ID,
        locator=locator,
        read_from=f"unknown:{SOURCE_ID}",
        derivation=Derivation(
            relation=relation,
            inputs=(source_path,),
            parameters=tuple(parameters),
            output_unit=QUANTITY_UNITS[quantity],
        ),
    )


def _row_temperature_tokens(record: Mapping[str, Any]) -> dict[int, RawToken]:
    result: dict[int, RawToken] = {}
    for token in _iter_raw_numeric_tokens(record):
        if token.column == "temperature" and token.row_index is not None:
            result[token.row_index] = token
    return result


def _row_usable_temperature(
    token: RawToken | None,
    grains: Mapping[tuple[str, str | None], Decimal] | None = None,
) -> tuple[Decimal | None, str | None]:
    if token is None:
        return None, "row has no temperature as_published token"
    reconstructed = _reconstructed_value(
        token.as_published, token.record_id, token=token, grains=grains
    )
    if reconstructed is not None:
        return reconstructed, None
    named = _named_damage_reason(token)
    if named:
        return None, named
    if token.ocr_suspect and not _image_verified(token):
        return None, (
            f"OCR-suspect temperature {token.as_published!r} without image-verified correction"
        )
    value = _published_decimal(token.as_published)
    if value is None:
        return None, f"unparseable temperature token {token.as_published!r}"
    return value, None


_NEIGHBOUR_SIGN_SKIP_COLUMNS = {
    "temperature",
    "table1_uncertainty",
    "table1_formation_enthalpy",
    "helgeson_corrected",
    "uncertainty_entropy",
    "uncertainty_planck_function",
    "uncertainty_heat_capacity",
    "uncertainty_formation_enthalpy",
    "uncertainty_formation_gibbs_energy",
    "uncertainty_log_kf",
    "enthalpy_298_minus_0",
}
NEIGHBOUR_SIGN_REFUSAL_REASON = (
    "neighbour-sign: candidate value is opposite in sign to both neighbouring "
    "printed values (dropped-minus / JANAF sign-loss class); refused, not stored"
)


def _signed_series(
    record: Mapping[str, Any],
) -> dict[tuple[str, str | None], list[tuple[int, RawToken, Decimal]]]:
    series: dict[tuple[str, str | None], list[tuple[int, RawToken, Decimal]]] = defaultdict(
        list
    )
    for token in _iter_raw_numeric_tokens(record):
        if token.row_index is None:
            continue
        if token.column in _NEIGHBOUR_SIGN_SKIP_COLUMNS:
            continue
        extracted = _extract_signed_number(token.as_published)
        if extracted is None or extracted == 0:
            continue
        series[(token.column, token.formation_basis)].append(
            (token.row_index, token, extracted)
        )
    return series


def _neighbours_of(
    token: RawToken,
    series: Mapping[tuple[str, str | None], list[tuple[int, RawToken, Decimal]]],
) -> tuple[Decimal | None, Decimal | None]:
    points = series.get((token.column, token.formation_basis), [])
    ordered = sorted(points, key=lambda item: item[0])
    index = next(
        (i for i, item in enumerate(ordered) if item[1] is token or item[0] == token.row_index),
        None,
    )
    if index is None:
        return None, None
    left = None
    right = None
    for previous in reversed(ordered[:index]):
        if previous[2] != 0:
            left = previous[2]
            break
    for following in ordered[index + 1 :]:
        if following[2] != 0:
            right = following[2]
            break
    return left, right


def _opposite_to_both_neighbours(
    value: Decimal, left: Decimal | None, right: Decimal | None
) -> bool:
    """True when value is the dropped-minus (or dropped-plus) class.

    Example: +111.855 sitting between −113.073 and −110.445 on a page headed
    FORMATION FROM THE OXIDES. The scan refuses that candidate; it is not
    advisory-only.
    """

    if left is None or right is None or value == 0:
        return False
    return (value > 0 > left and right < 0) or (value < 0 < left and right > 0)


def _neighbour_sign_hits(
    record: Mapping[str, Any],
    series: Mapping[tuple[str, str | None], list[tuple[int, RawToken, Decimal]]]
    | None = None,
) -> list[dict[str, Any]]:
    if series is None:
        series = _signed_series(record)
    hits: list[dict[str, Any]] = []
    for (column, basis), points in series.items():
        ordered = sorted(points, key=lambda item: item[0])
        for row_index, token, value in ordered:
            left, right = _neighbours_of(token, series)
            if not _opposite_to_both_neighbours(value, left, right):
                continue
            hits.append(
                {
                    "record_id": token.record_id,
                    "column": column,
                    "formation_basis": basis,
                    "row_index": row_index,
                    "as_published": token.as_published,
                    "extracted": str(value),
                    "left_neighbour": str(left),
                    "right_neighbour": str(right),
                }
            )
    return hits


def generate_record(payload: Mapping[str, Any]) -> RecordGeneration:
    """Transform one committed B1544 JSON record into observations and a report."""

    record_id = str(payload.get("record_id") or "")
    if not record_id:
        raise ValueError("B1544 record is missing record_id")
    tokens = list(_iter_raw_numeric_tokens(payload))
    grains = _column_grain_map(payload)
    temperature_by_row = _row_temperature_tokens(payload)
    usable_t: dict[int, Decimal] = {}
    unusable_t: dict[int, str] = {}
    for row_index, t_token in temperature_by_row.items():
        value, reason = _row_usable_temperature(t_token, grains)
        if value is None:
            unusable_t[row_index] = reason or "unusable temperature"
        else:
            usable_t[row_index] = value

    identity_results: list[dict[str, Any]] = []
    identity_fail_10x: set[tuple[int, str, str]] = set()
    identity_notice: dict[tuple[int, str, str], tuple[Decimal, Decimal]] = {}
    rows = payload.get("rows") or []
    if isinstance(rows, list):
        for row_index, row in enumerate(rows):
            if not isinstance(row, Mapping) or "formation" not in row:
                continue
            t_token = temperature_by_row.get(row_index)
            if row_index in unusable_t or t_token is None:
                identity_results.append(
                    {
                        "row_index": row_index,
                        "identity": "skipped",
                        "reason": unusable_t.get(row_index, "missing temperature"),
                    }
                )
                continue
            t_recon = _usable_reconstruction(t_token, grains)
            t_text = (
                format(t_recon["value"], "f")
                if t_recon is not None
                else t_token.as_published
            )
            entropy_cell = _as_published_cell(row.get("entropy"))
            hht_cell = _as_published_cell(row.get("enthalpy_increment_over_T"))
            gef_cell = _as_published_cell(row.get("planck_function"))
            entropy_tok = _token_lookup(tokens, row_index, "entropy", None)
            hht_tok = _token_lookup(tokens, row_index, "enthalpy_increment_over_T", None)
            gef_tok = _token_lookup(tokens, row_index, "planck_function", None)
            entropy_text = _identity_token_text(
                entropy_cell, record_id, token=entropy_tok, grains=grains
            )
            hht_text = _identity_token_text(
                hht_cell, record_id, token=hht_tok, grains=grains
            )
            gef_text = _identity_token_text(
                gef_cell, record_id, token=gef_tok, grains=grains
            )
            if entropy_text and hht_text and gef_text:
                residual, tolerance, calculated = _gibbs_identity(
                    entropy_text, hht_text, gef_text
                )
                result = {
                    "row_index": row_index,
                    "temperature_as_published": t_text,
                    "identity": "planck_vs_S_minus_HHT",
                    "printed": gef_cell[0] if gef_cell else gef_text,
                    "calculated": str(calculated),
                    "absolute_residual": str(residual),
                    "rounding_tolerance": str(tolerance),
                    "ok": residual <= tolerance,
                }
                identity_results.append(result)
                if residual > 10 * tolerance:
                    # Participating cells of -(G−H298)/T = S − (H−H298)/T.
                    # Detection must precede vocab-gap exclusion so S and
                    # (H−H298)/T refuse on a 10× error instead of storing.
                    identity_fail_10x.add((row_index, "planck_function", "shared"))
                    identity_fail_10x.add((row_index, "entropy", "shared"))
                    identity_fail_10x.add(
                        (row_index, "enthalpy_increment_over_T", "shared")
                    )
                elif residual > tolerance:
                    identity_notice[(row_index, "planck_function", "shared")] = (
                        residual,
                        tolerance,
                    )
            formation = row.get("formation") or {}
            if isinstance(formation, Mapping):
                for basis, block in formation.items():
                    if not isinstance(block, Mapping):
                        continue
                    dg_cell = _as_published_cell(block.get("gibbs_energy"))
                    logk_cell = _as_published_cell(block.get("log_kf"))
                    dg_tok = _token_lookup(
                        tokens, row_index, "formation_gibbs_energy", str(basis)
                    )
                    logk_tok = _token_lookup(tokens, row_index, "log_kf", str(basis))
                    dg_text = _identity_token_text(
                        dg_cell, record_id, token=dg_tok, grains=grains
                    )
                    logk_text = _identity_token_text(
                        logk_cell, record_id, token=logk_tok, grains=grains
                    )
                    if dg_text is None or logk_text is None:
                        continue
                    checked = _logk_identity(t_text, dg_text, logk_text)
                    if checked is None:
                        continue
                    residual, tolerance, calculated = checked
                    identity_results.append(
                        {
                            "row_index": row_index,
                            "temperature_as_published": t_text,
                            "identity": "log10_Kf_from_delta_fG",
                            "formation_basis": str(basis),
                            "printed": logk_cell[0],
                            "calculated": str(calculated),
                            "absolute_residual": str(residual),
                            "rounding_tolerance": str(tolerance),
                            "ok": residual <= tolerance,
                        }
                    )
                    if residual > 10 * tolerance:
                        identity_fail_10x.add((row_index, "log_kf", str(basis)))
                        identity_fail_10x.add(
                            (row_index, "formation_gibbs_energy", str(basis))
                        )
                    elif residual > tolerance:
                        identity_notice[(row_index, "log_kf", str(basis))] = (
                            residual,
                            tolerance,
                        )
                        identity_notice[
                            (row_index, "formation_gibbs_energy", str(basis))
                        ] = (residual, tolerance)

    table1_uncertainties: dict[tuple[int, str], RawToken] = {}
    grid_uncertainties: dict[tuple[str, str], RawToken] = {}
    for token in tokens:
        if token.column == "table1_uncertainty" and token.row_index is not None:
            table1_uncertainties[(token.row_index, token.table1_source or "")] = token
        if token.column.startswith("uncertainty_"):
            if token.ocr_suspect and not _image_verified(token):
                continue
            if _named_damage_reason(token):
                continue
            if token.as_published.strip() in {"-", "—"}:
                continue
            grid_column = token.column[len("uncertainty_") :]
            grid_uncertainties[(token.formation_basis or "shared", grid_column)] = token

    observations: list[Observation] = []
    refusals: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    vocabulary_gaps: list[dict[str, Any]] = []
    stored = 0
    refused = 0
    excluded = 0
    by_column: dict[str, Counter[str]] = defaultdict(Counter)
    by_column_basis: dict[str, Counter[str]] = defaultdict(Counter)

    def account(token: RawToken, bucket: str) -> None:
        by_column[token.column][bucket] += 1
        by_column[token.column]["raw"] += 1
        key = f"{token.column}:{token.formation_basis or 'shared'}"
        by_column_basis[key][bucket] += 1
        by_column_basis[key]["raw"] += 1

    def refuse(token: RawToken, reason: str) -> None:
        nonlocal refused
        refused += 1
        account(token, "refused")
        refusals.append(
            {
                "record_id": token.record_id,
                "path": token.path,
                "column": token.column,
                "formation_basis": token.formation_basis,
                "row_index": token.row_index,
                "as_published": token.as_published,
                "reason": reason,
            }
        )

    def exclude(token: RawToken, reason: str, *, vocabulary_gap: bool = False) -> None:
        nonlocal excluded
        excluded += 1
        account(token, "excluded")
        row = {
            "record_id": token.record_id,
            "path": token.path,
            "column": token.column,
            "formation_basis": token.formation_basis,
            "row_index": token.row_index,
            "as_published": token.as_published,
            "reason": reason,
        }
        exclusions.append(row)
        if vocabulary_gap:
            vocabulary_gaps.append(row)

    is_table1 = record_id == "usgs-b1544-table-1"
    table1_rows = payload.get("rows") or []
    neighbour_series = _signed_series(payload)
    neighbour_hits = _neighbour_sign_hits(payload, neighbour_series)

    for token in tokens:
        if (
            token.row_index is not None
            and token.column
            in STORED_GRID_COLUMNS | {"table1_formation_enthalpy", "helgeson_corrected"}
            and token.row_index in unusable_t
            and token.column != "temperature"
        ):
            refuse(
                token,
                f"row temperature token unusable: {unusable_t[token.row_index]}",
            )
            continue
        reconstruction = _reconstruction(token)
        if reconstruction is not None:
            recon_value = _reconstruction_decimal(reconstruction)
            grain_reason = _reconstruction_coarser_than_column(
                recon_value, token, grains
            )
            if grain_reason is not None:
                refuse(token, grain_reason)
                continue
        if reconstruction is None:
            named_or_ocr = _named_damage_reason(token)
            if named_or_ocr:
                tail_grain = _letter_tail_grain_reason(token, grains)
                if tail_grain and "grain" not in named_or_ocr:
                    named_or_ocr = f"{named_or_ocr}; {tail_grain}"
                refuse(token, named_or_ocr)
                continue
            if token.ocr_suspect and not _image_verified(token):
                refuse(
                    token,
                    f"OCR-suspect token {token.as_published!r} without image-verified correction",
                )
                continue
        stored_candidate = (
            reconstruction["value"]
            if reconstruction is not None
            else _published_decimal(token.as_published)
        )
        if isinstance(stored_candidate, str):
            stored_candidate = Decimal(stored_candidate)
        # Detection precedes exclusion. A 10× identity failure or a
        # dropped-minus must refuse even if the column is later a vocab gap
        # (planck, (H−H298)/T). Round 1 excluded those columns first, so S
        # stored on a 10× Gibbs-function error. Neighbour-sign uses the
        # candidate stored value (reconstructed −111.855 is not a dropped minus).
        identity_key = (
            token.row_index if token.row_index is not None else -1,
            token.column,
            token.formation_basis or "shared",
        )
        if identity_key in identity_fail_10x:
            refuse(
                token,
                "identity residual exceeds 10× printed-rounding tolerance; value not stored",
            )
            continue
        if isinstance(stored_candidate, Decimal):
            left, right = _neighbours_of(token, neighbour_series)
            if _opposite_to_both_neighbours(stored_candidate, left, right):
                refuse(token, NEIGHBOUR_SIGN_REFUSAL_REASON)
                continue
        if token.as_published.strip() in {"-", "—"}:
            exclude(token, "TABLE 1 headnote: [-,value not given]")
            continue
        if token.column == "temperature":
            exclude(
                token,
                "row coordinate; stored as identity.temperature_K on sibling observations",
            )
            continue
        if token.column in VOCABULARY_GAP_COLUMNS:
            exclude(
                token,
                VOCABULARY_GAP_COLUMNS[token.column],
                vocabulary_gap=True,
            )
            continue
        if token.column.startswith("uncertainty_") or token.column == "table1_uncertainty":
            exclude(token, UNCERTAINTY_EXCLUDE_REASON)
            continue
        stored_column = token.column in STORED_GRID_COLUMNS or token.column in {
            "table1_formation_enthalpy",
            "helgeson_corrected",
        }
        if not stored_column:
            continue
        value = stored_candidate if isinstance(stored_candidate, Decimal) else None
        if value is None:
            refuse(token, f"unparseable printed token {token.as_published!r}")
            continue
        quantity = COLUMN_QUANTITY[token.column]
        temperature_reconstruction = None
        if is_table1:
            temperature = Decimal("298.15")
            table1_phase = None
            if token.row_index is not None and isinstance(table1_rows, list):
                row = table1_rows[token.row_index]
                if isinstance(row, Mapping):
                    table1_phase = str(row.get("phase_as_published") or "")
        else:
            if token.row_index not in usable_t:
                refuse(token, "row temperature token unusable")
                continue
            temperature = usable_t[token.row_index]
            table1_phase = None
            t_token = temperature_by_row.get(token.row_index)
            if t_token is not None:
                temperature_reconstruction = _usable_reconstruction(t_token, grains)
        notices: tuple[Notice, ...] = ()
        notice_key = (
            token.row_index if token.row_index is not None else -1,
            token.column,
            token.formation_basis or "shared",
        )
        if notice_key in identity_notice:
            residual, tolerance = identity_notice[notice_key]
            notices = (
                _identity_notice(
                    quantity=quantity,
                    residual=residual,
                    tolerance=tolerance,
                    identity_name=(
                        "planck_vs_S_minus_HHT"
                        if token.column == "planck_function"
                        else "log10_Kf_from_delta_fG"
                    ),
                    basis=token.formation_basis,
                    temperature=str(temperature),
                ),
            )
        uncertainty = Uncertainty(kind=UncertaintyKind.NONE)
        if token.column == "table1_formation_enthalpy" and token.row_index is not None:
            sibling = table1_uncertainties.get(
                (token.row_index, token.table1_source or "")
            )
            if sibling is not None:
                uncertainty = Uncertainty(
                    kind=UncertaintyKind.PRINTED,
                    verbatim=sibling.as_published,
                )
        elif token.column in STORED_GRID_COLUMNS:
            if token.formation_basis:
                sibling = grid_uncertainties.get(
                    (token.formation_basis, token.column)
                )
            else:
                sibling = grid_uncertainties.get(
                    ("from_the_elements", token.column)
                ) or grid_uncertainties.get(("from_the_oxides", token.column))
            if sibling is not None:
                uncertainty = Uncertainty(
                    kind=UncertaintyKind.PRINTED,
                    verbatim=sibling.as_published,
                )
        observations.append(
            _observation(
                record=payload,
                token=token,
                quantity=quantity,
                value=value,
                temperature=temperature,
                notices=notices,
                table1_phase=table1_phase,
                uncertainty=uncertainty,
                reconstruction=reconstruction,
                temperature_reconstruction=temperature_reconstruction,
            )
        )
        stored += 1
        account(token, "stored")

    unexplained = len(tokens) - stored - refused - excluded
    if unexplained:
        raise AssertionError(
            f"{record_id}: unexplained numeric tokens: source={len(tokens)} "
            f"stored={stored} refused={refused} excluded={excluded}"
        )

    report = {
        "record_id": record_id,
        "formula": payload.get("formula_as_published"),
        "formation_bases": list(payload.get("formation_bases") or ()),
        "gas_constant_J_per_mol_K": str(B1544_R_J_PER_MOL_K),
        "gas_constant_basis": B1544_R_SOURCE,
        "unit_row": {
            "quote": UNIT_ROW_QUOTE,
            "locator": UNIT_ROW_LOCATOR_TEXT,
        },
        "standard_pressure": {
            "quote": TITLE_PRESSURE_QUOTE,
            "standard_pressure_Pa": str(STANDARD_PRESSURE_PA),
        },
        "cell_accounting": {
            "raw_numeric_tokens": len(tokens),
            "stored": stored,
            "refused": refused,
            "excluded": excluded,
            "unexplained": 0,
            "by_column": {key: dict(value) for key, value in sorted(by_column.items())},
            "by_column_and_basis": {
                key: dict(value) for key, value in sorted(by_column_basis.items())
            },
        },
        "refusals": refusals,
        "exclusions": exclusions,
        "vocabulary_gaps": vocabulary_gaps,
        "identity_results": identity_results,
        "neighbour_sign_hits": neighbour_hits,
    }
    return RecordGeneration(tuple(observations), report)


def load_record(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path}: record is not a mapping")
    return payload


def documents_from_records(directory: Path) -> Iterable[Mapping[str, Any]]:
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise ValueError(f"{directory}: no B1544 JSON records")
    for path in paths:
        yield load_record(path)


def write_staging(documents: Iterable[Mapping[str, Any]], out: Path) -> Mapping[str, Any]:
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out}: staging directory must be empty")
    out.mkdir(parents=True, exist_ok=True)
    observations: list[object] = []
    reports: list[object] = []
    quantity_counts: Counter[str] = Counter()
    stored_total = 0
    refused_total = 0
    excluded_total = 0
    raw_total = 0
    identity_fail_counts: Counter[str] = Counter()
    notice_count = 0
    started = time.monotonic()
    last_progress = started
    record_count = 0
    for record_count, document in enumerate(documents, start=1):
        generated = generate_record(document)
        observations.extend(to_plain(obs) for obs in generated.observations)
        reports.append(to_plain(generated.report))
        for observation in generated.observations:
            quantity = observation.identity.quantity.value
            quantity_counts[quantity.value] += 1
            notice_count += len(observation.notices)
        accounting = generated.report["cell_accounting"]
        stored_total += int(accounting["stored"])
        refused_total += int(accounting["refused"])
        excluded_total += int(accounting["excluded"])
        raw_total += int(accounting["raw_numeric_tokens"])
        for result in generated.report["identity_results"]:
            if result.get("ok") is False:
                identity_fail_counts[str(result.get("identity"))] += 1
        now = time.monotonic()
        if now - last_progress >= 60:
            print(
                f"B1544 generator: {record_count} records in {now - started:.1f}s",
                flush=True,
            )
            last_progress = now
    if record_count == 0:
        raise ValueError("no B1544 documents supplied")
    dump_yaml(
        {
            "schema_version": "battery_observations.v2.1",
            "source_id": SOURCE_ID,
            "observations": observations,
        },
        out / "observations" / "usgs-b1544.yaml",
    )
    dump_yaml(
        {
            "schema_version": "usgs_b1544_generator_report.v1",
            "source_id": SOURCE_ID,
            "records": reports,
        },
        out / "reports" / "usgs-b1544.yaml",
    )
    summary = {
        "schema_version": "usgs_b1544_generator_summary.v1",
        "source_id": SOURCE_ID,
        "record_count": record_count,
        "observation_counts_by_quantity": dict(sorted(quantity_counts.items())),
        "cell_accounting": {
            "raw_numeric_tokens": raw_total,
            "stored": stored_total,
            "refused": refused_total,
            "excluded": excluded_total,
            "unexplained": 0,
        },
        "identity_failure_counts": dict(sorted(identity_fail_counts.items())),
        "notice_count": notice_count,
        "gas_constant_J_per_mol_K": str(B1544_R_J_PER_MOL_K),
        "gas_constant_basis": B1544_R_SOURCE,
    }
    dump_yaml(summary, out / "summary.yaml")
    print(f"B1544 generator: wrote {record_count} records to {out}", flush=True)
    return summary


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--records",
        type=Path,
        default=RECORDS_DIR,
        help="directory of committed B1544 JSON records",
    )
    parser.add_argument("--out", type=Path, required=True, help="empty staging directory")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    write_staging(documents_from_records(args.records), args.out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
