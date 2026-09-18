"""Generate schema-v2.1 observations from USGS Bulletin 1452 records."""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from simulator.accounting.formulas import parse_formula
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
from simulator.battery.generators.usgs_b1544 import (
    LETTER_TAIL_RE,
    _decimal_grain,
    _gibbs_identity,
    _opposite_to_both_neighbours,
    _published_decimal,
    _value_grain,
)
from simulator.battery.identity import log10K_from_delta_fG_kJ_mol
from simulator.battery.migrate import dump_yaml, fill_identity, make_species, to_plain
from simulator.battery.polymorph_dictionary import resolve_printed_name_polymorph
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
from simulator.reference_data.robie_hemingway_fisher_1978_usgs_b1452_loader import (
    COMPILATION_ROOT,
    ROLE,
    SOURCE_ID,
)

RECORDS_DIR = COMPILATION_ROOT / "records"
SOURCE_PATH_PREFIX = (
    "data/literature/compilations/robie-hemingway-fisher-1978-usgs-b1452/records"
)
STANDARD_PRESSURE_PA = Decimal("100000")
# B1452 Table 1 (PDF p. 9 / printed p. 3), symbol R:
# "Gas constant, 8.3143 ± 0.0008 J· K-1· mol-1."
B1452_R_J_PER_MOL_K = Decimal("8.3143")
B1452_R_SOURCE = (
    "USGS B1452 Table 1 symbol R (PDF p. 9 / printed p. 3): "
    "'Gas constant, 8.3143 ± 0.0008 J· K-1· mol-1.' "
    "Never substitute a modern CODATA constant. Eq. (4) on PDF p. 15 / "
    "printed p. 10 uses 2.30258 R = 19.1444 with ΔG in joules."
)
HT_UNIT_ROW_QUOTE = "J/mol·K and kJ/mol"
HT_UNIT_ROW_LOCATOR = "PDF page 36 (printed p. 30) unit row"
TABLE1_UNIT_LOCATOR = "Table 1 PDF p. 9 / printed p. 3"
TITLE_PRESSURE_QUOTE = "298.15 K and 1 bar (10^5 pascals) pressure"
CIRCULARITY_WARNING = ROLE["circularity_warning"]
TABLE_298K_RECORD_ID = "robie-hemingway-fisher-1978-usgs-b1452-0003"
TABLE1_RECORD_ID = "robie-hemingway-fisher-1978-usgs-b1452-0001"
TABLE2_RECORD_ID = "robie-hemingway-fisher-1978-usgs-b1452-0002"
REFERENCE_TEMPERATURE_K = Decimal("298.15")

# Page-header units. Captured units_as_published on B1452 are OCR-destroyed
# (J/aol•K, kJ/1101, …). Never map a stored unit from the captured string.
HT_PAGE_UNITS = {
    "temperature": "K",
    "enthalpy_function": "J/mol·K",
    "entropy": "J/mol·K",
    "negative_gibbs_function": "J/mol·K",
    "heat_capacity": "J/mol·K",
    "formation_enthalpy": "kJ/mol",
    "formation_gibbs_energy": "kJ/mol",
    "log_kf": "dimensionless",
}
# Table 1 (printed p. 3) assigns 298.15 K table units. ΔHf / ΔGf are J·mol-1,
# not the HT kJ·mol-1. A silent 1000× error if the HT header is reused here.
TABLE_298K_PAGE_UNITS = {
    "formula_weight": "g/mol",
    "entropy_s298": "J/mol·K",
    "molar_volume": "cm^3/mol",
    "formation_enthalpy": "J/mol",
    "formation_gibbs_energy": "J/mol",
    "log_kf": "dimensionless",
    "reference_delta_h": "reference code",
    "reference_delta_g": "reference code",
    "reference_cp": "reference code",
}
STORED_HT_COLUMNS = frozenset(
    {"entropy", "heat_capacity", "formation_enthalpy", "formation_gibbs_energy", "log_kf"}
)
STORED_298K_COLUMNS = frozenset(
    {"entropy_s298", "formation_enthalpy", "formation_gibbs_energy", "log_kf"}
)
COLUMN_QUANTITY = {
    "entropy": Quantity.S,
    "entropy_s298": Quantity.S,
    "heat_capacity": Quantity.CP,
    "formation_enthalpy": Quantity.DELTA_FH,
    "formation_gibbs_energy": Quantity.DELTA_FG,
    "log_kf": Quantity.LOG10_KF,
}
VOCABULARY_GAP_COLUMNS = {
    "enthalpy_function": (
        "printed (H°T−H°298)/T in J/mol·K; schema v2.1 names H_minus_H298 as "
        "kJ/mol of H(T)−H(298), not the printed /T function. Not transformed."
    ),
    "negative_gibbs_function": (
        "printed −(G°T−H°298)/T is the Gibbs-function transcription check "
        "−(G−H298)/T = S − (H−H298)/T; not stored"
    ),
    "molar_volume": (
        "printed v298 in cm^3 (Table 1); schema v2.1 has no molar-volume quantity"
    ),
    "formula_weight": (
        "printed formula weight; schema v2.1 has no formula-weight quantity"
    ),
    "atomic_weight": (
        "Table 2 1975 atomic weight; schema v2.1 has no atomic-weight quantity"
    ),
    "atomic_weight_right": (
        "Table 2 1975 atomic weight; schema v2.1 has no atomic-weight quantity"
    ),
    "reference_delta_h": "printed literature-reference code, not a quantity",
    "reference_delta_g": "printed literature-reference code, not a quantity",
    "reference_cp": "printed literature-reference code, not a quantity",
}
TEXT_COLUMNS = frozenset(
    {
        "name_and_formula",
        "symbol",
        "definition",
        "element",
        "element_right",
        "symbol_right",
    }
)
FORMATION_FROM_THE_ELEMENTS = (
    "B1452 heading FORMATION FROM THE ELEMENTS (methods, printed p. 8); "
    "unmarked T-grids and the 298.15 K table are this convention. Schema v2.1 "
    "has no closed token for it."
)
FORMATION_FROM_THE_OXIDES = (
    "B1452 heading FORMATION FROM THE OXIDES plus asterisks on ΔfH and ΔfG "
    "(methods, printed p. 8). Schema v2.1 has no closed token for it."
)
FORMATION_BASIS_REASON = {
    "from_the_elements": FORMATION_FROM_THE_ELEMENTS,
    "from_the_oxides": FORMATION_FROM_THE_OXIDES,
}
MISSING_FORMATION_BASIS_REASON = (
    "source does not state formation basis; unknown is not a default"
)
UNCERTAINTY_EXCLUDE_REASON = (
    "printed uncertainty; attached to stored sibling observations of the matching "
    "column (298.15 K per-row when a grain-unique merged split exists; T-grid "
    "table-level uncertainty_values). Not itself a stored quantity."
)
NEIGHBOUR_SIGN_REFUSAL_REASON = (
    "neighbour-sign: candidate value is opposite in sign to both neighbouring "
    "printed values (dropped-minus / JANAF sign-loss class); refused, not stored"
)
NEIGHBOUR_SIGN_DISABLED_REASON = (
    "neighbour-sign is disabled on the 298.15 K properties table: recon measured "
    "merged value+uncertainty integers whose magnitudes are garbage, so a "
    "neighbour-sign scan is not a sign-loss detector there. Applied only to "
    "high-T T-grids, where the recon found 3 log Kf and 2 ΔfG hits."
)
MERGED_SPLIT_METHOD = "split_merged_value_uncertainty_by_column_grain"
FORMULA_UNRESOLVED_REASON_PREFIX = "no page-grounded formula;"
FORMULA_CONFLICT_REASON_PREFIX = "conflicting page-grounded formulas;"

_OXIDE_STAR_RE = re.compile(r"\s*\*\s*$")
_TRAILING_JUNK_RE = re.compile(r"[^0-9eE.+-]+$")
_LATEX_MATH_RE = re.compile(r"\$([^$]+)\$")
_LATEX_SUB_RE = re.compile(r"_\{([^}]+)\}|_([A-Za-z0-9]+)")
_LATEX_SUP_RE = re.compile(r"\^\{([^}]+)\}|\^([A-Za-z0-9+\-*]+)")
_DIATOMIC_GASES = frozenset({"O", "H", "N", "F", "Cl"})
# Cation → (oxide formula, cations per oxide formula). FeO first; leftover
# oxygen retries Fe2O3. Used only when the printed heading/asterisks say
# FORMATION FROM THE OXIDES and the formula parses.
_OXIDE_BY_CATION = {
    "Al": ("Al2O3", 2),
    "Si": ("SiO2", 1),
    "Ca": ("CaO", 1),
    "Mg": ("MgO", 1),
    "Na": ("Na2O", 2),
    "K": ("K2O", 2),
    "Li": ("Li2O", 2),
    "Ti": ("TiO2", 1),
    "Mn": ("MnO", 1),
    "Be": ("BeO", 1),
    "Zn": ("ZnO", 1),
    "Ba": ("BaO", 1),
    "Sr": ("SrO", 1),
    "Zr": ("ZrO2", 1),
    "Cr": ("Cr2O3", 2),
    "B": ("B2O3", 2),
    "P": ("P2O5", 2),
    "Pb": ("PbO", 1),
}


@dataclass(frozen=True)
class RecordGeneration:
    observations: tuple[Observation, ...]
    report: Mapping[str, Any]


@dataclass(frozen=True)
class FormulaResolution:
    formula: str | None
    source: str | None
    consulted: Mapping[str, Any]
    reason: str | None


@dataclass(frozen=True)
class RawToken:
    record_id: str
    path: str
    as_published: str
    ocr_suspect: bool
    footnote_markers: tuple[str, ...]
    column: str
    formation_basis: str | None
    row_index: int | None
    table_kind: str


def _table_kind(record: Mapping[str, Any]) -> str:
    record_id = str(record.get("record_id") or "")
    census = str(record.get("census_id") or "")
    if record_id == TABLE1_RECORD_ID or census.startswith("table-1"):
        return "table1_symbols"
    if record_id == TABLE2_RECORD_ID or census.startswith("table-2"):
        return "table2_weights"
    if record_id == TABLE_298K_RECORD_ID or census == "thermodynamic-properties-at-298-15-k":
        return "table_298k"
    return "ht_grid"


def _as_published_cell(cell: object) -> tuple[str, bool, tuple[str, ...]] | None:
    if not isinstance(cell, Mapping) or "as_published" not in cell:
        return None
    token = cell.get("as_published")
    if token is None:
        return None
    markers = tuple(str(marker) for marker in (cell.get("footnote_markers") or ()))
    return str(token), bool(cell.get("ocr_suspect")), markers


def _normalized_letters(text: str) -> str:
    return re.sub(r"[^A-Z]", "", text.upper())


def _header_text(record: Mapping[str, Any]) -> str:
    parts = [str(record.get("formation_basis_header_as_published") or "")]
    parts.extend(str(line) for line in (record.get("column_header_lines_as_published") or ()))
    return "\n".join(parts)


def _record_has_oxide_asterisks(record: Mapping[str, Any]) -> bool:
    for row in record.get("rows") or ():
        if not isinstance(row, Mapping):
            continue
        cells = row.get("cells") if isinstance(row.get("cells"), Mapping) else {}
        for column in ("formation_enthalpy", "formation_gibbs_energy"):
            parsed = _as_published_cell(cells.get(column))
            if parsed is None:
                continue
            token, _suspect, markers = parsed
            if "*" in token or "*" in markers:
                return True
    return False


def _formation_basis_for_record(record: Mapping[str, Any]) -> str | None:
    kind = _table_kind(record)
    if kind in {"table1_symbols", "table2_weights"}:
        return None
    if kind == "table_298k":
        # Table 1: ΔHf / ΔGf "from the elements in their standard reference states".
        return "from_the_elements"
    if _record_has_oxide_asterisks(record):
        return "from_the_oxides"
    letters = _normalized_letters(_header_text(record))
    if "OXIDE" in letters:
        return "from_the_oxides"
    if "ELEMENT" in letters or "ELE" in letters:
        return "from_the_elements"
    # Unmarked T-grids are the elements tables (methods, printed p. 8). Oxide
    # pages carry asterisks and/or the oxides heading. This is the page
    # convention, not a missing-JSON-key default.
    return "from_the_elements"


def _page_units(kind: str, column: str) -> str:
    if kind == "table_298k":
        return TABLE_298K_PAGE_UNITS.get(column, "")
    if kind == "ht_grid":
        return HT_PAGE_UNITS.get(column, "")
    return ""


def _strip_oxide_marker(token: str) -> str:
    return _OXIDE_STAR_RE.sub("", token.strip())


def _numeric_payload(token: str) -> str:
    text = _strip_oxide_marker(token)
    text = _TRAILING_JUNK_RE.sub("", text)
    return text.strip()


def _iter_raw_numeric_tokens(record: Mapping[str, Any]) -> list[RawToken]:
    """Count printed cells from as_published text only — never cell['value']."""

    record_id = str(record.get("record_id") or "")
    kind = _table_kind(record)
    basis = _formation_basis_for_record(record)
    tokens: list[RawToken] = []

    def add(
        path: str,
        cell: object,
        column: str,
        *,
        row_index: int | None = None,
        formation_basis: str | None = None,
    ) -> None:
        parsed = _as_published_cell(cell)
        if parsed is None:
            return
        token, suspect, markers = parsed
        column_basis = formation_basis
        if column in {"formation_enthalpy", "formation_gibbs_energy", "log_kf"}:
            column_basis = formation_basis if formation_basis is not None else basis
        tokens.append(
            RawToken(
                record_id=record_id,
                path=path,
                as_published=token,
                ocr_suspect=suspect,
                footnote_markers=markers,
                column=column,
                formation_basis=column_basis,
                row_index=row_index,
                table_kind=kind,
            )
        )

    add("formula_weight", record.get("formula_weight"), "formula_weight")
    for index, item in enumerate(record.get("uncertainty_values") or ()):
        add(f"uncertainty_values[{index}]", item, "uncertainty")
    rows = record.get("rows") or []
    if not isinstance(rows, list):
        raise ValueError(f"{record_id}: rows must be a list")
    column_ids = list(record.get("column_ids") or ())
    for row_index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"{record_id}: rows[{row_index}] is not a mapping")
        cells = row.get("cells") if isinstance(row.get("cells"), Mapping) else {}
        for column in column_ids:
            add(
                f"rows[{row_index}].cells.{column}",
                cells.get(column),
                column,
                row_index=row_index,
            )
    return tokens


@lru_cache(maxsize=1)
def _corrections_by_token() -> dict[tuple[str, str, str], Mapping[str, Any]]:
    manifest = yaml.safe_load((COMPILATION_ROOT / "manifest.yaml").read_text(encoding="utf-8"))
    index: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for item in manifest.get("corrections") or ():
        if not isinstance(item, Mapping):
            continue
        if item.get("action") != "image_verified_correction":
            continue
        record_id = str(item.get("record_id") or "")
        column = str(item.get("column") or "")
        ocr = str(item.get("ocr_token") or item.get("as_published") or "")
        index[(record_id, column, ocr)] = item
    return index


def _image_correction(token: RawToken) -> Mapping[str, Any] | None:
    return _corrections_by_token().get(
        (token.record_id, token.column, token.as_published)
    ) or _corrections_by_token().get(
        (token.record_id, token.column, _strip_oxide_marker(token.as_published))
    )


def _image_verified(token: RawToken) -> bool:
    return _image_correction(token) is not None


def _column_grain_map(
    tokens: Sequence[RawToken],
) -> dict[tuple[str, str | None], Decimal]:
    buckets: dict[tuple[str, str | None], list[Decimal]] = defaultdict(list)
    for token in tokens:
        if token.column in TEXT_COLUMNS:
            continue
        if _image_correction(token) is not None:
            printed = str(_image_correction(token).get("printed_token") or "")
            value = _published_decimal(_numeric_payload(printed))
            if value is None:
                continue
            buckets[(token.column, token.formation_basis)].append(_value_grain(value))
            continue
        if token.ocr_suspect:
            continue
        value = _published_decimal(_numeric_payload(token.as_published))
        if value is None:
            continue
        buckets[(token.column, token.formation_basis)].append(_value_grain(value))
    return {
        key: Counter(values).most_common(1)[0][0]
        for key, values in buckets.items()
        if values
    }


def _unique_grain_split(
    text: str, value_grain: Decimal, unc_grain: Decimal
) -> tuple[Decimal, Decimal] | None:
    """Split a glued value+uncertainty only when both halves match printed grain.

    `42.550.21` with entropy grain 0.01 is uniquely 42.55 and 0.21.
    `10575085` with integer grain 1 has many integer/integer cuts — refuse.
    """

    payload = text.strip()
    if not payload:
        return None
    matches: list[tuple[Decimal, Decimal]] = []
    for index in range(1, len(payload)):
        left_text = payload[:index]
        right_text = payload[index:]
        left = _published_decimal(left_text)
        right = _published_decimal(right_text)
        if left is None or right is None:
            continue
        if right <= 0:
            continue
        if _value_grain(left) != value_grain:
            continue
        if _value_grain(right) != unc_grain:
            continue
        matches.append((left, right))
    unique = set(matches)
    if len(unique) != 1:
        return None
    return next(iter(unique))


def _merged_split(
    token: RawToken, grains: Mapping[tuple[str, str | None], Decimal]
) -> dict[str, Any] | None:
    payload = _numeric_payload(token.as_published)
    if not payload or _published_decimal(payload) is not None:
        return None
    grain = grains.get((token.column, token.formation_basis))
    if grain is None:
        return None
    if payload.count(".") < 2 and grain == 1:
        return None
    split = _unique_grain_split(payload, grain, grain)
    if split is None:
        return None
    value, uncertainty = split
    return {
        "value": value,
        "uncertainty": uncertainty,
        "method_class": MERGED_SPLIT_METHOD,
        "evidence": (
            f"merged value+uncertainty {token.as_published!r} splits uniquely "
            f"at column printed grain {grain} into {value} ± {uncertainty}"
        ),
    }


def _letter_tail_grain_reason(
    token: RawToken, grains: Mapping[tuple[str, str | None], Decimal]
) -> str | None:
    text = _numeric_payload(token.as_published)
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


def _reconstruction_from_image(token: RawToken) -> dict[str, Any] | None:
    item = _image_correction(token)
    if item is None:
        return None
    printed = str(item.get("printed_token") or "")
    value = _published_decimal(_numeric_payload(printed))
    if value is None and item.get("value") is not None:
        try:
            value = Decimal(str(item["value"]))
        except InvalidOperation:
            value = None
    if value is None:
        return None
    return {
        "value": value,
        "method_class": "reconstructed_from_printed_page",
        "evidence": (
            f"image-verified correction of {token.as_published!r} to printed "
            f"{printed!r} ({item.get('evidence')})"
        ),
        "as_published": token.as_published,
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
        f"grain {grain} for {token.column}; as_published={token.as_published!r} "
        f"reconstructed={value}"
    )


def _oxide_marker_only_damage(token: RawToken) -> bool:
    """True when the only non-numeric mark is the oxide-basis asterisk.

    Methods (printed p. 8): asterisks to the right of ΔfH / ΔfG mark FORMATION
    FROM THE OXIDES. They are not OCR damage. B1544 round-1 refused them; do not.
    """

    if "*" not in token.as_published and "*" not in token.footnote_markers:
        return False
    return _published_decimal(_numeric_payload(token.as_published)) is not None


def _candidate_value(
    token: RawToken, grains: Mapping[tuple[str, str | None], Decimal]
) -> tuple[Decimal | None, dict[str, Any] | None, str | None]:
    """Return (value, reconstruction, refuse_reason)."""

    image = _reconstruction_from_image(token)
    if image is not None:
        grain_reason = _reconstruction_coarser_than_column(image["value"], token, grains)
        if grain_reason is not None:
            return None, image, grain_reason
        return image["value"], image, None
    tail = _letter_tail_grain_reason(token, grains)
    if tail:
        return None, None, tail
    payload = _numeric_payload(token.as_published)
    if LETTER_TAIL_RE.fullmatch(payload or token.as_published.strip()):
        return None, None, f"OCR letter tail {token.as_published!r}"
    merged = _merged_split(token, grains)
    if merged is not None:
        grain_reason = _reconstruction_coarser_than_column(merged["value"], token, grains)
        if grain_reason is not None:
            return None, merged, grain_reason
        return merged["value"], merged, None
    if token.ocr_suspect and not _oxide_marker_only_damage(token) and not _image_verified(token):
        return None, None, (
            f"OCR-suspect token {token.as_published!r} without image-verified correction"
        )
    value = _published_decimal(payload)
    if value is None:
        if payload.count(".") >= 2:
            grain = grains.get((token.column, token.formation_basis))
            return None, None, (
                f"merged value+uncertainty {token.as_published!r} has no unique split "
                f"at column printed grain {grain}"
            )
        if not payload:
            return None, None, None
        return None, None, f"unparseable printed token {token.as_published!r}"
    return value, None, None


def _identity_token_text(
    token: RawToken | None, grains: Mapping[tuple[str, str | None], Decimal]
) -> str | None:
    if token is None:
        return None
    value, _recon, reason = _candidate_value(token, grains)
    if reason is not None or value is None:
        return None
    return format(value, "f")


def _logk_identity(
    temperature_token: str,
    gibbs_token: str,
    logk_token: str,
    *,
    gibbs_page_unit: str,
) -> tuple[Decimal, Decimal, Decimal] | None:
    """log10 Kf = −ΔfG / (R T ln 10).

    Premise: B1452 Table 1 (PDF p. 9 / printed p. 3) prints
    R = 8.3143 ± 0.0008 J·K⁻¹·mol⁻¹ and eq. (4) (PDF p. 15 / printed p. 10)
    log Kf,T = −ΔG°f,T / (2.30258 R T) = −ΔG°f,T / 19.1444 T
    with ΔG in **joules** (19.1444 ≈ 2.30258 × 8.3143).

    Algebra: log10 Kf = −ΔfG_J / (R T ln 10)
           = −1000 × ΔfG_kJ / (R T ln 10).
    Unit check: J / ((J/(mol·K)) · K) is dimensionless.

    Worked 298.15 K row (Kyanite, 298 K table): printed ΔfG = −2441276 J·mol⁻¹
    = −2441.276 kJ·mol⁻¹.
    log10 Kf = 2441276 / (8.3143 × 298.15 × ln 10) ≈ 427.701 vs printed 427.703.

    Worked HT row (Silver, 400 K, printed p. 30): element ΔfG = 0 (formation
    zeros); Gibbs-function identity S − (H−H298)/T = 50.08 − 6.530 = 43.55
    vs printed −(G−H298)/T = 43.55.
    """

    temperature = Decimal(temperature_token)
    if temperature <= 0:
        return None
    gibbs = Decimal(gibbs_token)
    logk = Decimal(logk_token)
    if gibbs_page_unit == "J/mol":
        gibbs_kJ = gibbs / Decimal("1000")
        gibbs_grain_kJ = _decimal_grain(gibbs_token) / Decimal("1000")
    elif gibbs_page_unit == "kJ/mol":
        gibbs_kJ = gibbs
        gibbs_grain_kJ = _decimal_grain(gibbs_token)
    else:
        raise ValueError(f"ΔfG page unit must be J/mol or kJ/mol, not {gibbs_page_unit!r}")
    calculated = log10K_from_delta_fG_kJ_mol(
        gibbs_kJ,
        temperature,
        gas_constant_J_per_mol_K=B1452_R_J_PER_MOL_K,
    )
    e_t = _decimal_grain(temperature_token) / 2
    if temperature - e_t <= 0:
        return None
    denominator = abs(
        log10K_from_delta_fG_kJ_mol(
            Decimal("1"),
            temperature - e_t,
            gas_constant_J_per_mol_K=B1452_R_J_PER_MOL_K,
        )
    )
    temperature_term = abs(calculated) * e_t / (temperature - e_t)
    tolerance = (
        _decimal_grain(logk_token) / 2
        + gibbs_grain_kJ / 2 * denominator
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
        origin="usgs-b1452-generator",
        original=residual,
        band="printed last-place rounding (1× < residual ≤ 10×)",
    )


def _token_lookup(
    tokens: Sequence[RawToken],
    row_index: int,
    column: str,
) -> RawToken | None:
    return next(
        (
            token
            for token in tokens
            if token.row_index == row_index and token.column == column
        ),
        None,
    )


def _row_temperature_tokens(tokens: Sequence[RawToken]) -> dict[int, RawToken]:
    result: dict[int, RawToken] = {}
    for token in tokens:
        if token.column == "temperature" and token.row_index is not None:
            result[token.row_index] = token
    return result


def _latex_to_formula(chunk: str) -> tuple[str, int | None]:
    charge: int | None = None
    superscript = _LATEX_SUP_RE.search(chunk)
    if superscript is not None:
        raw = superscript.group(1) or superscript.group(2) or ""
        if raw and set(raw) <= {"*"}:
            charge = len(raw)
        elif raw in {"+", "-"}:
            charge = 1 if raw == "+" else -1
        elif raw.endswith("+") and raw[:-1].isdigit():
            charge = int(raw[:-1] or "1")
        elif raw.endswith("-") and raw[:-1].isdigit():
            charge = -int(raw[:-1] or "1")
        elif raw.endswith("+"):
            charge = 1
        elif raw.endswith("-"):
            charge = -1
        chunk = chunk[: superscript.start()] + chunk[superscript.end() :]
    chunk = chunk.replace(r"\cdot", ".").replace(r"\mathrm", "")
    chunk = _LATEX_SUB_RE.sub(lambda match: match.group(1) or match.group(2), chunk)
    chunk = chunk.replace("{", "").replace("}", "").replace("\\", "")
    chunk = chunk.replace(" ", "")
    return chunk, charge


def _formula_from_name(name: str) -> tuple[str | None, int | None]:
    charge: int | None = None
    for chunk in _LATEX_MATH_RE.findall(name):
        formula, latex_charge = _latex_to_formula(chunk)
        if latex_charge is not None:
            charge = latex_charge
        if formula and not formula.startswith("Delta") and _looks_like_formula(formula):
            return formula, charge
        if formula and latex_charge is not None and re.fullmatch(r"[A-Z][a-z]?", formula):
            return formula, charge
    tokens = name.replace("(", " ").replace(")", " ").split()
    if tokens:
        last = tokens[-1]
        # Last-token formulas are "Ag" or "Fe2SiO4", not words that happen to
        # parse as element soup (CUBIC → C,U,B,I,C).
        if (
            (any(character.isdigit() for character in last) or re.fullmatch(r"[A-Z][a-z]?", last))
            and _looks_like_formula(last)
        ):
            return last, charge
    return None, charge


def _parseable_formula(text: str) -> str | None:
    compact = re.sub(r"\s+", "", text.replace("·", "."))
    if not compact or compact.startswith("$"):
        return None
    for candidate in (compact, compact.replace("0", "O")):
        try:
            parse_formula(candidate)
        except Exception:
            continue
        return candidate
    return None


def _looks_like_formula(text: str) -> bool:
    return _parseable_formula(text) is not None


@lru_cache(maxsize=1)
def _formula_index_from_298k() -> dict[str, str]:
    """First-word → formula, only when that word is unique among 298 K rows.

    Rows without an extractable formula still veto the key. Otherwise COBALT
    (metal, no formula on the name line) silently maps to Co3O4 from COBALT
    SPINEL.
    """

    formulas: dict[str, set[str]] = defaultdict(set)
    formula_less: dict[str, int] = defaultdict(int)
    for name, _fw, formula in _iter_298k_name_rows():
        key = _name_key(name)
        if not key:
            continue
        if formula:
            formulas[key].add(formula)
        else:
            formula_less[key] += 1
    return {
        key: next(iter(forms))
        for key, forms in formulas.items()
        if len(forms) == 1 and formula_less.get(key, 0) == 0
    }


@lru_cache(maxsize=1)
def _formula_weight_index_from_298k() -> dict[str, str]:
    formulas: dict[str, set[str]] = defaultdict(set)
    for _name, fw, formula in _iter_298k_name_rows():
        if fw and formula:
            formulas[fw].add(formula)
    return {
        fw: next(iter(forms)) for fw, forms in formulas.items() if len(forms) == 1
    }


def _iter_298k_name_rows() -> tuple[tuple[str, str | None, str | None], ...]:
    path = RECORDS_DIR / f"{TABLE_298K_RECORD_ID}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    rows: list[tuple[str, str | None, str | None]] = []
    for row in record.get("rows") or ():
        cells = row.get("cells") if isinstance(row, Mapping) else {}
        parsed = _as_published_cell((cells or {}).get("name_and_formula"))
        if parsed is None:
            continue
        fw_parsed = _as_published_cell((cells or {}).get("formula_weight"))
        fw = fw_parsed[0].strip() if fw_parsed and fw_parsed[0].strip() else None
        formula, _charge = _formula_from_name(parsed[0])
        rows.append((parsed[0], fw, formula))
    return tuple(rows)


@lru_cache(maxsize=1)
def _table2_elements() -> tuple[dict[str, Decimal], dict[str, str]]:
    path = RECORDS_DIR / f"{TABLE2_RECORD_ID}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    weights: dict[str, Decimal] = {}
    names: dict[str, str] = {}
    for row in record.get("rows") or ():
        cells = row.get("cells") if isinstance(row, Mapping) else {}
        for name_col, symbol_col, weight_col in (
            ("element", "symbol", "atomic_weight"),
            ("element_right", "symbol_right", "atomic_weight_right"),
        ):
            name_parsed = _as_published_cell((cells or {}).get(name_col))
            symbol_parsed = _as_published_cell((cells or {}).get(symbol_col))
            weight_parsed = _as_published_cell((cells or {}).get(weight_col))
            if symbol_parsed is None:
                continue
            symbol = symbol_parsed[0].strip()
            if not symbol:
                continue
            if weight_parsed is not None:
                mass = _published_decimal(weight_parsed[0])
                if mass is not None:
                    weights[symbol] = mass
            if name_parsed is not None and name_parsed[0].strip():
                names[name_parsed[0].strip()] = symbol
    return weights, names


def _name_key(name: str) -> str:
    text = _LATEX_MATH_RE.sub("", name)
    text = re.sub(r"\([^)]*\)", " ", text)
    words = [word for word in re.split(r"[^A-Za-z]+", text) if word]
    if not words:
        return ""
    return words[0].upper()


def _formula_weight_as_published(
    record: Mapping[str, Any], *, row_index: int | None = None
) -> str | None:
    kind = _table_kind(record)
    if kind == "table_298k" and row_index is not None:
        rows = record.get("rows") or []
        if isinstance(rows, list) and row_index < len(rows):
            row = rows[row_index]
            cells = row.get("cells") if isinstance(row, Mapping) else {}
            parsed = _as_published_cell((cells or {}).get("formula_weight"))
            if parsed is not None and parsed[0].strip():
                return parsed[0].strip()
        return None
    parsed = _as_published_cell(record.get("formula_weight"))
    if parsed is not None and parsed[0].strip():
        return parsed[0].strip()
    return None


def _formula_mass(formula: str) -> Decimal | None:
    counts = _parse_counts(formula)
    if counts is None:
        return None
    weights, _names = _table2_elements()
    total = Decimal("0")
    for element, amount in counts.items():
        mass = weights.get(element)
        if mass is None:
            return None
        total += mass * Decimal(amount.numerator) / Decimal(amount.denominator)
    return total


def _mass_matches_formula_weight(formula: str, fw: Decimal) -> bool:
    mass = _formula_mass(formula)
    if mass is None:
        return False
    grain = max(_value_grain(fw) * 2, Decimal("0.01"))
    return abs(mass - fw) <= grain


def _allotrope_from_weight(name: str, fw: Decimal) -> str | None:
    weights, names = _table2_elements()
    hits: set[str] = set()
    for element_name, symbol in names.items():
        if not re.search(
            rf"(?<![A-Za-z]){re.escape(element_name)}(?![A-Za-z])",
            name,
            flags=re.IGNORECASE,
        ):
            continue
        aw = weights.get(symbol)
        if aw is None or aw == 0:
            continue
        n = (fw / aw).to_integral_value()
        if n < 1:
            continue
        formula = symbol if n == 1 else f"{symbol}{int(n)}"
        if _mass_matches_formula_weight(formula, fw):
            hits.add(formula)
    if len(hits) == 1:
        return next(iter(hits))
    return None


def _allotrope_from_printed_element(printed: str, fw: Decimal) -> str | None:
    counts = _parse_counts(printed)
    if counts is None or len(counts) != 1:
        return None
    symbol = next(iter(counts))
    weights, _names = _table2_elements()
    aw = weights.get(symbol)
    if aw is None or aw == 0:
        return None
    n = (fw / aw).to_integral_value()
    if n < 1:
        return None
    formula = symbol if n == 1 else f"{symbol}{int(n)}"
    if _mass_matches_formula_weight(formula, fw):
        return formula
    return None


def _consulted_formula_reason(
    consulted: Mapping[str, Any], *, prefix: str, extra: str | None = None
) -> str:
    parts = [
        f"formula_as_published={consulted.get('formula_as_published')!r}",
        f"name={consulted.get('name')!r}",
        f"name_key={consulted.get('name_key')!r}",
        f"formula_weight={consulted.get('formula_weight')!r}",
        f"name_index={consulted.get('name_index')!r}",
        f"formula_weight_index={consulted.get('formula_weight_index')!r}",
    ]
    mismatch = consulted.get("printed_mass_mismatch")
    if mismatch:
        parts.append(f"printed_mass_mismatch={mismatch!r}")
    if extra:
        parts.append(extra)
    return f"{prefix} consulted " + ", ".join(parts)


def _resolve_formula(
    record: Mapping[str, Any],
    *,
    name: str | None = None,
    formula_weight: str | None = None,
) -> FormulaResolution:
    """Page-grounded formula or a refusal. Never title-case an OCR'd name."""

    label = name if name is not None else str(record.get("name_as_published") or "")
    raw = record.get("formula_as_published")
    printed_raw = raw.strip() if isinstance(raw, str) and raw.strip() else None
    fw_text = formula_weight if formula_weight is not None else _formula_weight_as_published(record)
    key = _name_key(label)
    embedded, _charge = _formula_from_name(label)
    indexed = _formula_index_from_298k().get(key) if key else None
    fw_indexed = _formula_weight_index_from_298k().get(fw_text) if fw_text else None
    consulted: dict[str, Any] = {
        "formula_as_published": printed_raw,
        "name": label or None,
        "name_key": key or None,
        "formula_weight": fw_text,
        "name_index": indexed,
        "formula_weight_index": fw_indexed,
        "name_embedded": embedded,
    }
    candidates: list[tuple[str, str]] = []
    if embedded:
        candidates.append((embedded, "name_embedded"))
    if indexed:
        candidates.append((indexed, "name_index"))
    if fw_indexed:
        candidates.append((fw_indexed, "formula_weight"))
    printed = _parseable_formula(re.sub(r"\s+", "", printed_raw)) if printed_raw else None
    consulted["printed_parsed"] = printed
    fw_value = _published_decimal(fw_text) if fw_text else None
    if printed:
        if fw_value is not None:
            mass = _formula_mass(printed)
            if mass is None or _mass_matches_formula_weight(printed, fw_value):
                candidates.append((printed, "printed_formula"))
            else:
                consulted["printed_mass_mismatch"] = (
                    f"{printed} mass={mass} formula_weight={fw_value}"
                )
                allotrope = _allotrope_from_printed_element(printed, fw_value)
                if allotrope:
                    candidates.append((allotrope, "formula_weight"))
        else:
            candidates.append((printed, "printed_formula"))
    if fw_value is not None:
        named_allotrope = _allotrope_from_weight(label, fw_value)
        if named_allotrope:
            candidates.append((named_allotrope, "formula_weight"))

    if fw_value is not None:
        matching = [
            item
            for item in candidates
            if _formula_mass(item[0]) is None or _mass_matches_formula_weight(item[0], fw_value)
        ]
        if matching:
            candidates = matching

    unique = {formula for formula, _source in candidates}
    if len(unique) == 1:
        formula = next(iter(unique))
        sources = sorted({source for value, source in candidates if value == formula})
        return FormulaResolution(formula, "+".join(sources), consulted, None)
    if len(unique) > 1:
        return FormulaResolution(
            None,
            None,
            consulted,
            _consulted_formula_reason(
                consulted,
                prefix=FORMULA_CONFLICT_REASON_PREFIX,
                extra=f"formulas={sorted(unique)}",
            ),
        )
    return FormulaResolution(
        None,
        None,
        consulted,
        _consulted_formula_reason(consulted, prefix=FORMULA_UNRESOLVED_REASON_PREFIX),
    )


def _formula_for(
    record: Mapping[str, Any],
    *,
    name: str | None = None,
    formula_weight: str | None = None,
) -> str | None:
    return _resolve_formula(record, name=name, formula_weight=formula_weight).formula


def _charge_for_name(name: str) -> int:
    _formula, charge = _formula_from_name(name)
    if charge is not None:
        return charge
    return 0


def _phase_from_text(text: str) -> tuple[State[Phase], State[Polymorph]]:
    lowered = text.lower()
    if "aqueous" in lowered or "aq." in lowered or "ion std" in lowered:
        return State.of(Phase.AQ), State.not_applicable("not crystal")
    if "ideal" in lowered and "gas" in lowered:
        return State.of(Phase.G), State.not_applicable("not crystal")
    if "liquid" in lowered and "crystal" not in lowered:
        return State.of(Phase.L), State.not_applicable("not crystal")
    if "gas" in lowered and "crystal" not in lowered and "liquid" not in lowered:
        return State.of(Phase.G), State.not_applicable("not crystal")
    return State.of(Phase.CR), _polymorph_from_text(text)


def _polymorph_from_text(text: str) -> State[Polymorph]:
    stripped = _LATEX_MATH_RE.sub("", text)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    parenthetical = re.findall(r"\(([^)]+)\)", stripped)
    candidates = [stripped]
    if stripped:
        candidates.append(stripped.split()[0])
    candidates.extend(parenthetical)
    for candidate in candidates:
        label = candidate.strip().lower()
        if not label or label in {"reference state", "ideal gas"}:
            continue
        token = resolve_printed_name_polymorph(label)
        if token is not None:
            return State.of(token)
        token = resolve_printed_name_polymorph(label.replace("-", " "))
        if token is not None:
            return State.of(token)
    if "reference state" in stripped.lower():
        token = resolve_printed_name_polymorph("reference")
        if token is not None:
            return State.of(token)
    return State.unknown(
        f"name_as_published {text!r} is not a closed Polymorph token"
    )


def _phase_state(
    record: Mapping[str, Any], *, name: str | None = None, row_index: int | None = None
) -> tuple[State[Phase], State[Polymorph]]:
    kind = _table_kind(record)
    if kind == "table_298k":
        return _phase_from_text(name or "")
    phase_text = str(record.get("phase_as_published") or "")
    name_text = str(record.get("name_as_published") or "")
    phase, polymorph = _phase_from_text(phase_text or name_text)
    if phase.is_value and phase.value is Phase.CR:
        named = _polymorph_from_text(name_text)
        if named.is_value:
            polymorph = named
    return phase, polymorph


def _parse_counts(formula: str) -> dict[str, Fraction] | None:
    try:
        parsed = parse_formula(formula)
    except Exception:
        return None
    counts: dict[str, Fraction] = {}
    for element, amount in parsed.elements.items():
        counts[str(element)] = Fraction(str(amount)).limit_denominator(1000)
    return counts or None


def _element_reference_species(symbol: str) -> Species:
    if symbol in _DIATOMIC_GASES:
        return make_species(f"{symbol}2", Phase.G, charge=0)
    token = resolve_printed_name_polymorph("reference")
    polymorph = (
        State.of(token)
        if token is not None
        else State.unknown("elemental reference crystals")
    )
    return make_species(symbol, Phase.CR, polymorph, charge=0)


def _oxide_species(formula: str) -> Species:
    token = resolve_printed_name_polymorph(
        {
            "Al2O3": "corundum",
            "SiO2": "quartz",
            "CaO": "lime",
            "MgO": "periclase",
            "TiO2": "rutile",
            "FeO": "wustite",
        }.get(formula, "")
    )
    polymorph = (
        State.of(token)
        if token is not None
        else State.unknown(f"oxide {formula} has no closed polymorph token")
    )
    phase = Phase.L if formula == "H2O" else Phase.CR
    if phase is Phase.L:
        polymorph = State.not_applicable("not crystal")
    return make_species(formula, phase, polymorph, charge=0)


def _formation_identity(
    product: Species, basis: str
) -> tuple[Reaction, tuple[tuple[str, Species], ...]] | None:
    counts = _parse_counts(product.formula)
    if counts is None:
        return None
    elements = tuple(
        (element, _element_reference_species(element)) for element in sorted(counts)
    )
    terms = [ReactionTerm(product, Fraction(1))]
    if basis == "from_the_elements":
        for element, amount in counts.items():
            if element in _DIATOMIC_GASES:
                terms.append(ReactionTerm(_element_reference_species(element), -amount / 2))
            else:
                terms.append(ReactionTerm(_element_reference_species(element), -amount))
        return Reaction(tuple(terms)), elements
    if basis != "from_the_oxides":
        return None
    iron_oxides = (("FeO", 1), ("Fe2O3", 2)) if "Fe" in counts else ()
    attempts: list[tuple[str, int]] = []
    oxygen_from_oxides = Fraction(0)
    try:
        for element, amount in counts.items():
            if element in {"O"}:
                continue
            if element == "H":
                n_water = amount / 2
                terms.append(ReactionTerm(_oxide_species("H2O"), -n_water))
                oxygen_from_oxides += n_water
                continue
            if element == "Fe":
                attempts.append(("Fe", int(amount)))
                continue
            spec = _OXIDE_BY_CATION.get(element)
            if spec is None:
                return None
            oxide_formula, cations_per = spec
            n_oxide = amount / cations_per
            terms.append(ReactionTerm(_oxide_species(oxide_formula), -n_oxide))
            oxygen_from_oxides += n_oxide * (
                _parse_counts(oxide_formula) or {}
            ).get("O", Fraction(0))
        if attempts:
            leftover_without_iron = counts.get("O", Fraction(0)) - oxygen_from_oxides
            amount = Fraction(attempts[0][1])
            chosen = None
            for oxide_formula, cations_per in iron_oxides:
                n_oxide = amount / cations_per
                oxygen = n_oxide * (_parse_counts(oxide_formula) or {}).get("O", Fraction(0))
                if leftover_without_iron - oxygen == 0:
                    chosen = (oxide_formula, n_oxide, oxygen)
                    break
            if chosen is None:
                return None
            oxide_formula, n_oxide, oxygen = chosen
            terms.append(ReactionTerm(_oxide_species(oxide_formula), -n_oxide))
            oxygen_from_oxides += oxygen
        leftover = counts.get("O", Fraction(0)) - oxygen_from_oxides
        if leftover != 0:
            return None
    except (ValueError, ZeroDivisionError):
        return None
    return Reaction(tuple(terms)), elements


def _basis_states(
    product: Species, basis: str | None
) -> tuple[State[Reaction], State[tuple[tuple[str, Species], ...]]]:
    if basis not in FORMATION_BASIS_REASON:
        return (
            State.unknown(MISSING_FORMATION_BASIS_REASON),
            State.unknown(MISSING_FORMATION_BASIS_REASON),
        )
    built = _formation_identity(product, basis)
    if built is None:
        reason = (
            f"formula {product.formula!r} does not parse to a closed {basis} reaction"
        )
        return State.unknown(reason), State.unknown(reason)
    reaction, elements = built
    return State.of(reaction), State.of(elements)


def _unit_row_locator(kind: str) -> Locator:
    if kind == "table_298k":
        return Locator(
            pdf_page_index=9,
            published_page=3,
            note=f"{TABLE1_UNIT_LOCATOR}: ΔHf and ΔGf in J·mol-1",
        )
    return Locator(
        pdf_page_index=36,
        published_page=30,
        note=f"{HT_UNIT_ROW_LOCATOR}: {HT_UNIT_ROW_QUOTE}",
    )


def _pages(record: Mapping[str, Any]) -> tuple[int | None, int | None]:
    locator = record.get("source_locator") if isinstance(record.get("source_locator"), Mapping) else {}
    printed = list(locator.get("printed_pages") or ())
    pdf = list(locator.get("pdf_pages") or ())
    return (printed[0] if printed else None), (pdf[0] if pdf else None)


def _store_value(token: RawToken, value: Decimal) -> Decimal:
    """Convert 298 K formation J·mol-1 into the schema kJ unit. HT is already kJ."""

    if token.table_kind == "table_298k" and token.column in {
        "formation_enthalpy",
        "formation_gibbs_energy",
    }:
        return value / Decimal("1000")
    return value


def _observation(
    *,
    record: Mapping[str, Any],
    token: RawToken,
    quantity: Quantity,
    value: Decimal,
    temperature: Decimal,
    notices: tuple[Notice, ...],
    name: str | None,
    formula: str,
    uncertainty: Uncertainty | None = None,
    reconstruction: Mapping[str, Any] | None = None,
) -> Observation:
    record_id = str(record["record_id"])
    phase, polymorph = _phase_state(record, name=name, row_index=token.row_index)
    charge = _charge_for_name(name or str(record.get("name_as_published") or ""))
    species = make_species(formula, phase, polymorph, charge=charge)
    basis = token.formation_basis
    known: dict[str, Any] = {
        "per": State.of(PerBasis.MOL_SPECIES),
        "temperature_K": State.of(temperature),
        "standard_pressure_Pa": State.of(STANDARD_PRESSURE_PA),
    }
    if quantity in {Quantity.DELTA_FH, Quantity.DELTA_FG, Quantity.LOG10_KF}:
        known["reaction"], known["formation_elements"] = _basis_states(species, basis)
    identity = fill_identity(quantity, species, **known)
    published_page, pdf_page = _pages(record)
    unit = _page_units(token.table_kind, token.column)
    source_path = f"{SOURCE_PATH_PREFIX}/{record_id}.json"
    unit_locator = (
        TABLE1_UNIT_LOCATOR if token.table_kind == "table_298k" else HT_UNIT_ROW_LOCATOR
    )
    note = (
        f"as_published={token.as_published!r}; unit={unit!r} quoted from {unit_locator}"
        f"; captured units_as_published ignored; standard_pressure=1 bar from title "
        f"({TITLE_PRESSURE_QUOTE})"
    )
    if basis:
        note += f"; formation_basis={basis}"
    if reconstruction is not None:
        note += (
            f"; reconstructed={reconstruction['value']!s} "
            f"method_class={reconstruction['method_class']}; "
            f"evidence={reconstruction.get('evidence')}"
        )
    locator = Locator(
        published_page=published_page,
        pdf_page_index=pdf_page,
        table=record_id,
        source_path=source_path,
        record=record_id,
        note=note,
    )
    role_note = (
        "compilation_role engine_reference_input=true, scoring_eligible=false; "
        f"circularity_warning={CIRCULARITY_WARNING}"
    )
    method_class = "assessed_thermodynamic_functions"
    relation = f"Direct B1452 tabulation; {role_note}"
    if basis and basis in FORMATION_BASIS_REASON:
        relation += f"; formation_basis={FORMATION_BASIS_REASON[basis]}"
    if reconstruction is not None:
        method_class = str(reconstruction["method_class"])
        relation += f"; reconstructed from {token.as_published!r} via {method_class}"
    evidence = Evidence(
        class_=State.of(EvidenceClass.COMPILATION_ASSESSED),
        original_method_class=method_class,
        model="assessed_thermodynamic_functions",
    )
    parameters: list[tuple[str, Located[Decimal]]] = []
    if token.table_kind == "ht_grid" and quantity in {Quantity.DELTA_FH, Quantity.DELTA_FG}:
        parameters.append(
            (
                "kj_to_j",
                Located(State.of(Decimal("1000")), locator=_unit_row_locator("ht_grid")),
            )
        )
    if token.table_kind == "table_298k" and quantity in {
        Quantity.DELTA_FH,
        Quantity.DELTA_FG,
    }:
        parameters.append(
            (
                "j_to_kj",
                Located(
                    State.of(Decimal("0.001")),
                    locator=_unit_row_locator("table_298k"),
                ),
            )
        )
    suffix = (
        f"{quantity.value}:{basis or 'shared'}:"
        f"T={temperature}:row={token.row_index}:col={token.column}"
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


def _extract_signed_number(token: str) -> Decimal | None:
    text = _numeric_payload(token)
    return _published_decimal(text)


def _signed_series(
    tokens: Sequence[RawToken],
) -> dict[tuple[str, str | None], list[tuple[int, RawToken, Decimal]]]:
    series: dict[tuple[str, str | None], list[tuple[int, RawToken, Decimal]]] = defaultdict(
        list
    )
    for token in tokens:
        if token.table_kind == "table_298k":
            continue
        if token.row_index is None:
            continue
        if token.column in TEXT_COLUMNS or token.column in {
            "temperature",
            "uncertainty",
            "formula_weight",
        }:
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
        (
            i
            for i, item in enumerate(ordered)
            if item[1] is token or item[0] == token.row_index
        ),
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


def _neighbour_sign_hits(
    tokens: Sequence[RawToken],
    series: Mapping[tuple[str, str | None], list[tuple[int, RawToken, Decimal]]],
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for _key, points in series.items():
        ordered = sorted(points, key=lambda item: item[0])
        for row_index, token, value in ordered:
            left, right = _neighbours_of(token, series)
            if not _opposite_to_both_neighbours(value, left, right):
                continue
            hits.append(
                {
                    "record_id": token.record_id,
                    "column": token.column,
                    "formation_basis": token.formation_basis,
                    "row_index": row_index,
                    "as_published": token.as_published,
                    "extracted": str(value),
                    "left_neighbour": str(left),
                    "right_neighbour": str(right),
                }
            )
    return hits


def _row_name(record: Mapping[str, Any], row_index: int | None) -> str | None:
    if row_index is None:
        return None
    rows = record.get("rows") or []
    if not isinstance(rows, list) or row_index >= len(rows):
        return None
    row = rows[row_index]
    if not isinstance(row, Mapping):
        return None
    cells = row.get("cells") if isinstance(row.get("cells"), Mapping) else {}
    parsed = _as_published_cell((cells or {}).get("name_and_formula"))
    if parsed is None:
        return None
    return parsed[0]


def _formula_resolution_report(
    kind: str,
    record_formula: FormulaResolution | None,
    formula_by_row: Mapping[int | None, FormulaResolution],
) -> dict[str, Any]:
    if kind == "table_298k":
        resolved = [item for item in formula_by_row.values() if item.formula]
        unresolved = [item for item in formula_by_row.values() if item.formula is None]
        return {
            "rows_resolved": len(resolved),
            "rows_unresolved": len(unresolved),
            "unresolved": len(unresolved) > 0,
            "sources": dict(Counter(item.source or "none" for item in resolved)),
        }
    if record_formula is None:
        return {"unresolved": False, "formula": None, "source": None}
    return {
        "formula": record_formula.formula,
        "source": record_formula.source,
        "consulted": dict(record_formula.consulted),
        "reason": record_formula.reason,
        "unresolved": record_formula.formula is None,
    }


def generate_record(payload: Mapping[str, Any]) -> RecordGeneration:
    """Transform one committed B1452 JSON record into observations and a report."""

    record_id = str(payload.get("record_id") or "")
    if not record_id:
        raise ValueError("B1452 record is missing record_id")
    kind = _table_kind(payload)
    tokens = list(_iter_raw_numeric_tokens(payload))
    grains = _column_grain_map(tokens)
    temperature_by_row = _row_temperature_tokens(tokens)
    usable_t: dict[int, Decimal] = {}
    unusable_t: dict[int, str] = {}
    if kind == "ht_grid":
        for row_index, t_token in temperature_by_row.items():
            value, _recon, reason = _candidate_value(t_token, grains)
            if value is None:
                unusable_t[row_index] = reason or "unusable temperature"
            else:
                usable_t[row_index] = value

    identity_results: list[dict[str, Any]] = []
    identity_fail_10x: set[tuple[int, str]] = set()
    identity_notice: dict[tuple[int, str], tuple[Decimal, Decimal]] = {}
    rows = payload.get("rows") or []
    if isinstance(rows, list) and kind in {"ht_grid", "table_298k"}:
        for row_index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                continue
            if kind == "ht_grid":
                if row_index in unusable_t:
                    identity_results.append(
                        {
                            "row_index": row_index,
                            "identity": "skipped",
                            "reason": unusable_t[row_index],
                        }
                    )
                    continue
                t_token = temperature_by_row.get(row_index)
                t_text = (
                    format(usable_t[row_index], "f")
                    if row_index in usable_t
                    else (t_token.as_published if t_token else None)
                )
                if t_text is None:
                    continue
                entropy_tok = _token_lookup(tokens, row_index, "entropy")
                hht_tok = _token_lookup(tokens, row_index, "enthalpy_function")
                gef_tok = _token_lookup(tokens, row_index, "negative_gibbs_function")
                entropy_text = _identity_token_text(entropy_tok, grains)
                hht_text = _identity_token_text(hht_tok, grains)
                gef_text = _identity_token_text(gef_tok, grains)
                if entropy_text and hht_text and gef_text:
                    residual, tolerance, calculated = _gibbs_identity(
                        entropy_text, hht_text, gef_text
                    )
                    identity_results.append(
                        {
                            "row_index": row_index,
                            "temperature_as_published": t_text,
                            "identity": "planck_vs_S_minus_HHT",
                            "printed": gef_tok.as_published if gef_tok else gef_text,
                            "calculated": str(calculated),
                            "absolute_residual": str(residual),
                            "rounding_tolerance": str(tolerance),
                            "ok": residual <= tolerance,
                        }
                    )
                    if residual > 10 * tolerance:
                        identity_fail_10x.add((row_index, "negative_gibbs_function"))
                        identity_fail_10x.add((row_index, "entropy"))
                        identity_fail_10x.add((row_index, "enthalpy_function"))
                    elif residual > tolerance:
                        identity_notice[(row_index, "negative_gibbs_function")] = (
                            residual,
                            tolerance,
                        )
                gibbs_unit = "kJ/mol"
            else:
                t_text = "298.15"
                gibbs_unit = "J/mol"
            dg_tok = _token_lookup(tokens, row_index, "formation_gibbs_energy")
            logk_tok = _token_lookup(tokens, row_index, "log_kf")
            dg_text = _identity_token_text(dg_tok, grains)
            logk_text = _identity_token_text(logk_tok, grains)
            if dg_text is None or logk_text is None:
                continue
            checked = _logk_identity(
                t_text, dg_text, logk_text, gibbs_page_unit=gibbs_unit
            )
            if checked is None:
                continue
            residual, tolerance, calculated = checked
            identity_results.append(
                {
                    "row_index": row_index,
                    "temperature_as_published": t_text,
                    "identity": "log10_Kf_from_delta_fG",
                    "formation_basis": dg_tok.formation_basis if dg_tok else None,
                    "printed": logk_tok.as_published if logk_tok else logk_text,
                    "calculated": str(calculated),
                    "absolute_residual": str(residual),
                    "rounding_tolerance": str(tolerance),
                    "ok": residual <= tolerance,
                    "gibbs_page_unit": gibbs_unit,
                }
            )
            if residual > 10 * tolerance:
                identity_fail_10x.add((row_index, "log_kf"))
                identity_fail_10x.add((row_index, "formation_gibbs_energy"))
                if kind == "table_298k":
                    # 298 K jammed integers glue value to uncertainty on every
                    # formation column of the row (recon: 10575085 / 77077100).
                    # log Kf identity is the scale detector; ΔfH has no log Kf
                    # identity of its own and must not store the glued integer.
                    identity_fail_10x.add((row_index, "formation_enthalpy"))
            elif residual > tolerance:
                identity_notice[(row_index, "log_kf")] = (residual, tolerance)
                identity_notice[(row_index, "formation_gibbs_energy")] = (
                    residual,
                    tolerance,
                )

    formula_by_row: dict[int | None, FormulaResolution] = {}
    record_formula: FormulaResolution | None = None
    if kind == "table_298k" and isinstance(rows, list):
        for row_index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                continue
            formula_by_row[row_index] = _resolve_formula(
                payload,
                name=_row_name(payload, row_index),
                formula_weight=_formula_weight_as_published(
                    payload, row_index=row_index
                ),
            )
    elif kind == "ht_grid":
        record_formula = _resolve_formula(payload)

    observations: list[Observation] = []
    refusals: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    vocabulary_gaps: list[dict[str, Any]] = []
    stored = 0
    refused = 0
    excluded = 0
    merged_split = 0
    merged_refused = 0
    by_column: dict[str, Counter[str]] = defaultdict(Counter)
    by_column_basis: dict[str, Counter[str]] = defaultdict(Counter)

    def account(token: RawToken, bucket: str) -> None:
        by_column[token.column][bucket] += 1
        by_column[token.column]["raw"] += 1
        key = f"{token.column}:{token.formation_basis or 'shared'}"
        by_column_basis[key][bucket] += 1
        by_column_basis[key]["raw"] += 1

    def refuse(token: RawToken, reason: str) -> None:
        nonlocal refused, merged_refused
        refused += 1
        if "merged" in reason:
            merged_refused += 1
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

    neighbour_series = _signed_series(tokens)
    neighbour_hits = _neighbour_sign_hits(tokens, neighbour_series)
    stored_columns = STORED_HT_COLUMNS if kind == "ht_grid" else STORED_298K_COLUMNS

    for token in tokens:
        stripped = token.as_published.strip()
        if token.column in TEXT_COLUMNS:
            exclude(token, "printed label / definition; not a numeric quantity cell")
            continue
        if stripped in {"", "-", "—"}:
            exclude(
                token,
                "blank printed cell" if stripped == "" else "printed blank marker",
            )
            continue
        if (
            kind == "ht_grid"
            and token.row_index is not None
            and token.column in stored_columns
            and token.row_index in unusable_t
        ):
            refuse(token, f"row temperature token unusable: {unusable_t[token.row_index]}")
            continue
        value, reconstruction, refuse_reason = _candidate_value(token, grains)
        if refuse_reason:
            refuse(token, refuse_reason)
            continue
        if reconstruction and reconstruction.get("method_class") == MERGED_SPLIT_METHOD:
            merged_split += 1
        identity_key = (token.row_index if token.row_index is not None else -1, token.column)
        if identity_key in identity_fail_10x:
            refuse(
                token,
                "identity residual exceeds 10× printed-rounding tolerance; value not stored",
            )
            continue
        if (
            kind != "table_298k"
            and isinstance(value, Decimal)
            and token.column not in TEXT_COLUMNS
        ):
            left, right = _neighbours_of(token, neighbour_series)
            if _opposite_to_both_neighbours(value, left, right):
                refuse(token, NEIGHBOUR_SIGN_REFUSAL_REASON)
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
        if token.column == "uncertainty":
            exclude(token, UNCERTAINTY_EXCLUDE_REASON)
            continue
        if kind in {"table1_symbols", "table2_weights"} and token.column not in {
            "atomic_weight",
            "atomic_weight_right",
        }:
            exclude(token, "auxiliary Table 1/2 cell; not a schema v2.1 quantity")
            continue
        if token.column not in stored_columns:
            exclude(token, f"column {token.column} is not a stored quantity")
            continue
        if value is None:
            refuse(token, f"unparseable printed token {token.as_published!r}")
            continue
        quantity = COLUMN_QUANTITY[token.column]
        if kind == "table_298k":
            temperature = REFERENCE_TEMPERATURE_K
        elif token.row_index not in usable_t:
            refuse(token, "row temperature token unusable")
            continue
        else:
            temperature = usable_t[token.row_index]
        if kind == "table_298k":
            resolution = formula_by_row.get(token.row_index)
        else:
            resolution = record_formula
        if resolution is None or resolution.formula is None:
            refuse(
                token,
                (resolution.reason if resolution is not None else None)
                or _consulted_formula_reason(
                    {"formula_as_published": payload.get("formula_as_published")},
                    prefix=FORMULA_UNRESOLVED_REASON_PREFIX,
                ),
            )
            continue
        notices: tuple[Notice, ...] = ()
        if identity_key in identity_notice:
            residual, tolerance = identity_notice[identity_key]
            notices = (
                _identity_notice(
                    quantity=quantity,
                    residual=residual,
                    tolerance=tolerance,
                    identity_name=(
                        "planck_vs_S_minus_HHT"
                        if token.column in {"negative_gibbs_function", "entropy", "enthalpy_function"}
                        else "log10_Kf_from_delta_fG"
                    ),
                    basis=token.formation_basis,
                    temperature=str(temperature),
                ),
            )
        uncertainty = Uncertainty(kind=UncertaintyKind.NONE)
        if reconstruction and reconstruction.get("uncertainty") is not None:
            uncertainty = Uncertainty(
                kind=UncertaintyKind.PRINTED,
                verbatim=str(reconstruction["uncertainty"]),
            )
        stored_point = _store_value(token, value)
        observations.append(
            _observation(
                record=payload,
                token=token,
                quantity=quantity,
                value=stored_point,
                temperature=temperature,
                notices=notices,
                name=_row_name(payload, token.row_index),
                formula=resolution.formula,
                uncertainty=uncertainty,
                reconstruction=reconstruction,
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
        "table_kind": kind,
        "formula": payload.get("formula_as_published"),
        "formation_basis": _formation_basis_for_record(payload),
        "gas_constant_J_per_mol_K": str(B1452_R_J_PER_MOL_K),
        "gas_constant_basis": B1452_R_SOURCE,
        "unit_row": {
            "ht_quote": HT_UNIT_ROW_QUOTE,
            "ht_locator": HT_UNIT_ROW_LOCATOR,
            "table1_locator": TABLE1_UNIT_LOCATOR,
            "table_298k_formation_unit": TABLE_298K_PAGE_UNITS["formation_enthalpy"],
            "ht_formation_unit": HT_PAGE_UNITS["formation_enthalpy"],
        },
        "standard_pressure": {
            "quote": TITLE_PRESSURE_QUOTE,
            "standard_pressure_Pa": str(STANDARD_PRESSURE_PA),
        },
        "neighbour_sign": {
            "applied": kind == "ht_grid",
            "disabled_on_298k": kind == "table_298k",
            "reason": NEIGHBOUR_SIGN_DISABLED_REASON,
        },
        "merged_cell_splits": {
            "split": merged_split,
            "refused": merged_refused,
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
        "formula_resolution": _formula_resolution_report(
            kind, record_formula, formula_by_row
        ),
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
        raise ValueError(f"{directory}: no B1452 JSON records")
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
    merged_split_total = 0
    merged_refused_total = 0
    identity_fail_counts: Counter[str] = Counter()
    identity_fail_10x = 0
    notice_count = 0
    formula_unresolved_records = 0
    formula_unresolved_rows = 0
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
        merged = generated.report["merged_cell_splits"]
        merged_split_total += int(merged["split"])
        merged_refused_total += int(merged["refused"])
        for result in generated.report["identity_results"]:
            if result.get("ok") is False:
                identity_fail_counts[str(result.get("identity"))] += 1
                residual = Decimal(str(result.get("absolute_residual") or "0"))
                tolerance = Decimal(str(result.get("rounding_tolerance") or "0"))
                if tolerance > 0 and residual > 10 * tolerance:
                    identity_fail_10x += 1
        now = time.monotonic()
        resolution = generated.report.get("formula_resolution") or {}
        if generated.report.get("table_kind") == "table_298k":
            formula_unresolved_rows += int(resolution.get("rows_unresolved") or 0)
        elif resolution.get("unresolved"):
            formula_unresolved_records += 1
        if now - last_progress >= 20 or record_count % 50 == 0:
            print(
                f"B1452 generator: {record_count} records in {now - started:.1f}s "
                f"stored={stored_total} refused={refused_total} excluded={excluded_total}",
                flush=True,
            )
            last_progress = now
    if record_count == 0:
        raise ValueError("no B1452 documents supplied")
    unexplained = raw_total - stored_total - refused_total - excluded_total
    if unexplained:
        raise AssertionError(
            f"B1452 census does not close: raw={raw_total} stored={stored_total} "
            f"refused={refused_total} excluded={excluded_total} unexplained={unexplained}"
        )
    dump_yaml(
        {
            "schema_version": "battery_observations.v2.1",
            "source_id": SOURCE_ID,
            "observations": observations,
        },
        out / "observations" / "usgs-b1452.yaml",
    )
    dump_yaml(
        {
            "schema_version": "usgs_b1452_generator_report.v1",
            "source_id": SOURCE_ID,
            "records": reports,
        },
        out / "reports" / "usgs-b1452.yaml",
    )
    summary = {
        "schema_version": "usgs_b1452_generator_summary.v1",
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
        "merged_cell_splits": {
            "split": merged_split_total,
            "refused": merged_refused_total,
        },
        "identity_failure_counts": dict(sorted(identity_fail_counts.items())),
        "identity_fail_10x": identity_fail_10x,
        "notice_count": notice_count,
        "formula_unresolved_ht_records": formula_unresolved_records,
        "formula_unresolved_298k_rows": formula_unresolved_rows,
        "gas_constant_J_per_mol_K": str(B1452_R_J_PER_MOL_K),
        "gas_constant_basis": B1452_R_SOURCE,
        "neighbour_sign_disabled_on": "table_298k",
        "neighbour_sign_disabled_reason": NEIGHBOUR_SIGN_DISABLED_REASON,
    }
    dump_yaml(summary, out / "summary.yaml")
    print(f"B1452 generator: wrote {record_count} records to {out}", flush=True)
    return summary


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--records",
        type=Path,
        default=RECORDS_DIR,
        help="directory of committed B1452 JSON records",
    )
    parser.add_argument("--out", type=Path, required=True, help="empty staging directory")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    write_staging(documents_from_records(args.records), args.out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
