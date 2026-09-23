"""Generate schema-v2.1 observations from USGS Bulletin 1259 records."""

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
    _opposite_to_both_neighbours,
    _published_decimal,
    _value_grain,
)
from simulator.battery.identity import (
    LN10,
    THERMOCHEMICAL_CALORIE_J,
    atm_to_pa,
    log10K_from_delta_fG_kJ_mol,
)
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
from simulator.physical_constants import STANDARD_ATMOSPHERE_PA
from simulator.reference_data.robie_waldbaum_1968_usgs_b1259_loader import (
    COMPILATION_ROOT,
    R_CAL,
    ROLE,
    SOURCE_ID,
)

RECORDS_DIR = COMPILATION_ROOT / "records"
TABLE2_RECORD_ID = "b1259-table-02-atomic-weights"
SOURCE_PATH_PREFIX = (
    "data/literature/compilations/robie-waldbaum-1968-usgs-b1259/records"
)
# Table 1 (PDF p. 9 / printed p. 3) symbol R:
# "Gas constant, 1.98717 ±.00030 cal deg-gfw 1, 8.31469 joules"
B1259_R_CAL = R_CAL
B1259_R_J_FROM_CAL = B1259_R_CAL * THERMOCHEMICAL_CALORIE_J
B1259_R_J_PRINTED = Decimal("8.31469")
B1259_R_SOURCE = (
    "USGS B1259 Table 1 symbol R (PDF p. 9 / printed p. 3): "
    "'Gas constant, 1.98717 ±.00030 cal deg-gfw 1, 8.31469 joules'. "
    "Transcription of printed calorie columns uses 1.98717 cal/(mol·K). "
    "The joule companion 8.31469 is not 1.98717 × 4.184 and is not the "
    "transcription constant. Never substitute a modern CODATA constant."
)
TITLE_PRESSURE_QUOTE = (
    "298.15 K (25.0 C) and one atmosphere (1.013 bars) pressure"
)
STANDARD_PRESSURE_PA = atm_to_pa(Decimal("1"))
TABLE1_UNIT_LOCATOR = "Table 1 PDF p. 9 / printed p. 3"
HT_UNIT_ROW_LOCATOR = (
    "high-T table header (units_as_published on each substance table; "
    "H−H298 and Δf in kcal gfw^-1, S and −(G−H298)/T in cal deg^-1 gfw^-1)"
)
HT_UNIT_ROW_QUOTE = "kcal gfw^-1 and cal deg^-1 gfw^-1"
CIRCULARITY_WARNING = ROLE["circularity_warning"]
REFERENCE_TEMPERATURE_K = Decimal("298.15")
CAL_TH_TO_J = THERMOCHEMICAL_CALORIE_J
CAL_TH_TO_KJ = THERMOCHEMICAL_CALORIE_J / Decimal("1000")

PAGE_UNITS_298K = {
    "entropy": "cal deg^-1 gfw^-1",
    "molar_volume": "cm^3",
    "delta_f_H": "cal gfw^-1",
    "delta_f_G": "cal gfw^-1",
    "log_Kf": "dimensionless",
    "gram_formula_weight": "g/gfw",
}
PAGE_UNITS_HT = {
    "temperature": "K",
    "H_minus_H298": "kcal gfw^-1",
    "entropy": "cal deg^-1 gfw^-1",
    "gibbs_function": "cal deg^-1 gfw^-1",
    "delta_f_H": "kcal gfw^-1",
    "delta_f_G": "kcal gfw^-1",
    "log_Kf": "dimensionless",
    "gram_formula_weight": "g/gfw",
}
STORED_298K_COLUMNS = frozenset({"entropy", "delta_f_H", "delta_f_G", "log_Kf"})
STORED_HT_COLUMNS = frozenset(
    {"H_minus_H298", "entropy", "delta_f_H", "delta_f_G", "log_Kf"}
)
COLUMN_QUANTITY = {
    "entropy": Quantity.S,
    "H_minus_H298": Quantity.H_MINUS_H298,
    "delta_f_H": Quantity.DELTA_FH,
    "delta_f_G": Quantity.DELTA_FG,
    "log_Kf": Quantity.LOG10_KF,
}
VALUE_COLUMNS_298K = (
    "entropy",
    "molar_volume",
    "delta_f_H",
    "delta_f_G",
    "log_Kf",
)
VALUE_COLUMNS_HT = (
    "temperature",
    "H_minus_H298",
    "entropy",
    "gibbs_function",
    "delta_f_H",
    "delta_f_G",
    "log_Kf",
)
GIBBS_FUNCTION_EXCLUDE_REASON = (
    "printed column is -(G_T-H_298)/T in cal deg^-1 gfw^-1 (Table 1 "
    "'Gibbs free energy function'; HT units_as_published quote "
    "'[printed -(G_T-H_298)/T]'). The stored number would be the already "
    "negated Planck function, not G(T)-H298 and not ΔfG. Identity checks "
    "the printed (negated) value against S - 1000(H-H298)/T; the column "
    "is not stored."
)
VOCABULARY_GAP_COLUMNS = {
    "gibbs_function": GIBBS_FUNCTION_EXCLUDE_REASON,
    "molar_volume": (
        "printed v298 in cm^3 (298.15 K table); schema v2.1 has no "
        "molar-volume quantity"
    ),
    "gram_formula_weight": (
        "printed gram formula weight; schema v2.1 has no formula-weight quantity"
    ),
    "atomic_weight": (
        "Table 2 1963 atomic weight; schema v2.1 has no atomic-weight quantity"
    ),
}
FORMATION_FROM_THE_ELEMENTS = (
    "B1259 Table 1 (PDF p. 9 / printed p. 3) defines ΔHf / ΔGf as heat / "
    "Gibbs free energy of formation from the reference state. Unmarked "
    "298.15 K rows and high-T grids are this convention. Schema v2.1 has "
    "no closed token for it."
)
FORMATION_BASIS_REASON = {"from_the_elements": FORMATION_FROM_THE_ELEMENTS}
MISSING_FORMATION_BASIS_REASON = (
    "source does not state formation basis; unknown is not a default"
)
UNCERTAINTY_EXCLUDE_REASON = (
    "printed uncertainty; not itself a stored quantity. Attached to stored "
    "sibling observations of the matching column when the uncertainty row "
    "names that column."
)
NEIGHBOUR_SIGN_REFUSAL_REASON = (
    "neighbour-sign: candidate value is opposite in sign to both neighbouring "
    "printed values (dropped-minus / JANAF sign-loss class); refused, not stored"
)
NEIGHBOUR_SIGN_DISABLED_REASON = (
    "neighbour-sign is disabled on the 298.15 K properties table: each "
    "record is a single substance row, so a neighbour scan is not a "
    "sign-loss detector there. Applied only to high-T T-grids."
)
MERGED_SPLIT_METHOD = "split_merged_value_uncertainty_by_column_grain"
UNGUARDED_MERGED_REASON = (
    "a stored merged split is unguarded when its (row, column) did not "
    "participate in a passing identity (ok or 1×–10× notice). 298 K "
    "entropy still has no (H−H298) or −(G−H298)/T, so Gibbs 10× cannot "
    "see those splits; HT H−H298 is guarded only when the Planck identity "
    "runs; HT ΔfH is not a log Kf 10× participant."
)
INTEGER_GLUE_NO_UNIQUE_SPLIT = (
    "merged value+uncertainty has no unique split at column printed grain; "
    "not stored as the glued integer"
)
FORMULA_UNRESOLVED_REASON_PREFIX = "no page-grounded formula;"
FORMULA_CONFLICT_REASON_PREFIX = "conflicting page-grounded formulas;"
_TRAILING_JUNK_RE = re.compile(r"[^0-9eE.+-]+$")
_DIATOMIC_GASES = frozenset({"O", "H", "N", "F", "Cl"})
_SUBSCRIPT_TRANSLATION = str.maketrans(
    {
        "₀": "0",
        "₁": "1",
        "₂": "2",
        "₃": "3",
        "₄": "4",
        "₅": "5",
        "₆": "6",
        "₇": "7",
        "₈": "8",
        "₉": "9",
    }
)
_NUMBERISH_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)")


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
    charge: int | None = None


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
    role: str = "value"


def _table_kind(record: Mapping[str, Any]) -> str:
    kind = str(record.get("table_kind") or "")
    if kind == "properties_298k":
        return "properties_298k"
    if kind == "high_temperature":
        return "high_temperature"
    if kind == "symbols_and_constants":
        return "table1_symbols"
    if kind == "atomic_weights_1963":
        return "table2_weights"
    if kind == "critical_summaries_bibliography":
        return "table3_bibliography"
    raise ValueError(
        f"{record.get('record_id')}: unrecognised table_kind {kind!r}"
    )


def _as_published_cell(cell: object) -> tuple[str, bool, tuple[str, ...]] | None:
    if not isinstance(cell, Mapping) or "as_published" not in cell:
        return None
    token = cell.get("as_published")
    if token is None:
        return None
    markers = tuple(str(marker) for marker in (cell.get("footnote_markers") or ()))
    return str(token), bool(cell.get("ocr_suspect")), markers


def _page_units(kind: str, column: str) -> str:
    if kind == "properties_298k":
        return PAGE_UNITS_298K.get(column, "")
    if kind == "high_temperature":
        return PAGE_UNITS_HT.get(column, "")
    return ""


def _numeric_payload(token: str) -> str:
    text = token.strip()
    text = _TRAILING_JUNK_RE.sub("", text)
    return text.strip()


def _iter_raw_numeric_tokens(record: Mapping[str, Any]) -> list[RawToken]:
    """Count printed cells from as_published text only — never cell['value']."""

    record_id = str(record.get("record_id") or "")
    kind = _table_kind(record)
    basis = "from_the_elements" if kind in {"properties_298k", "high_temperature"} else None
    tokens: list[RawToken] = []

    def add(
        path: str,
        cell: object,
        column: str,
        *,
        row_index: int | None = None,
        role: str = "value",
        formation_basis: str | None = None,
    ) -> None:
        parsed = _as_published_cell(cell)
        if parsed is None:
            return
        token, suspect, markers = parsed
        column_basis = formation_basis
        if column in {"delta_f_H", "delta_f_G", "log_Kf"}:
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
                role=role,
            )
        )

    def add_string(
        path: str,
        token: str,
        column: str,
        *,
        role: str,
        row_index: int | None = None,
    ) -> None:
        add(
            path,
            {"as_published": token, "ocr_suspect": False, "footnote_markers": []},
            column,
            row_index=row_index,
            role=role,
        )

    add("gram_formula_weight", record.get("gram_formula_weight"), "gram_formula_weight")

    if kind == "properties_298k":
        for column in VALUE_COLUMNS_298K:
            add(column, record.get(column), column, row_index=0)
        for index, item in enumerate(record.get("uncertainty_line_tokens") or ()):
            text = str(item)
            if _NUMBERISH_RE.fullmatch(text.strip().lstrip("±").strip() or "") or _published_decimal(
                text.strip().lstrip("±")
            ):
                payload = text.strip().lstrip("±").strip()
                add_string(
                    f"uncertainty_line_tokens[{index}]",
                    payload or text,
                    "uncertainty",
                    role="uncertainty",
                    row_index=0,
                )
        for index, item in enumerate(record.get("references_as_published") or ()):
            text = str(item).strip()
            if text and _published_decimal(text) is not None:
                add_string(
                    f"references_as_published[{index}]",
                    text,
                    "reference_code",
                    role="reference",
                )
        return tokens

    if kind == "high_temperature":
        rows = record.get("rows") or []
        if not isinstance(rows, list):
            raise ValueError(f"{record_id}: rows must be a list")
        for row_index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise ValueError(f"{record_id}: rows[{row_index}] is not a mapping")
            row_kind = str(row.get("kind") or "data")
            role = "uncertainty" if row_kind == "uncertainty" else "value"
            for column in VALUE_COLUMNS_HT:
                cell = row.get(column)
                if cell is None:
                    continue
                add(
                    f"rows[{row_index}].{column}",
                    cell,
                    "uncertainty" if role == "uncertainty" else column,
                    row_index=row_index,
                    role=role,
                )
        footer = record.get("footer") if isinstance(record.get("footer"), Mapping) else {}
        for key, value in (footer or {}).items():
            if not str(key).endswith("_as_published"):
                continue
            text = str(value or "").strip()
            if not text:
                continue
            if _published_decimal(text) is None:
                continue
            add_string(f"footer.{key}", text, str(key), role="footer")
        return tokens

    if kind == "table1_symbols":
        rows = record.get("rows") or []
        if not isinstance(rows, list):
            raise ValueError(f"{record_id}: rows must be a list")
        for row_index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                continue
            for index, item in enumerate(row.get("numeric_tokens") or ()):
                add(
                    f"rows[{row_index}].numeric_tokens[{index}]",
                    item,
                    "table1_numeric",
                    row_index=row_index,
                    role="auxiliary",
                )
        return tokens

    if kind == "table2_weights":
        rows = record.get("rows") or []
        if not isinstance(rows, list):
            raise ValueError(f"{record_id}: rows must be a list")
        for row_index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                continue
            add(
                f"rows[{row_index}].weight",
                row.get("weight"),
                "atomic_weight",
                row_index=row_index,
                role="auxiliary",
            )
        return tokens

    return tokens


def _column_grain_map(
    tokens: Sequence[RawToken],
) -> dict[tuple[str, str | None], Decimal]:
    buckets: dict[tuple[str, str | None], list[Decimal]] = defaultdict(list)
    for token in tokens:
        if token.role != "value":
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


def _grain_valid_splits(
    text: str, value_grain: Decimal, unc_grain: Decimal
) -> list[tuple[Decimal, Decimal]]:
    payload = text.strip()
    if not payload:
        return []
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
    return matches


def _unique_grain_split(
    text: str, value_grain: Decimal, unc_grain: Decimal
) -> tuple[Decimal, Decimal] | None:
    """Split a glued value+uncertainty only when both halves match printed grain.

    `42.550.21` with entropy grain 0.01 is uniquely 42.55 and 0.21.
    `10575085` with integer grain 1 has many integer/integer cuts — refuse.
    """

    unique = set(_grain_valid_splits(text, value_grain, unc_grain))
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


def _candidate_value(
    token: RawToken, grains: Mapping[tuple[str, str | None], Decimal]
) -> tuple[Decimal | None, dict[str, Any] | None, str | None]:
    """Return (value, reconstruction, refuse_reason)."""

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
    if token.ocr_suspect:
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
    """Identity consumes the reconstructed value, never the raw jammed token."""

    if token is None:
        return None
    value, _recon, reason = _candidate_value(token, grains)
    if reason is not None or value is None:
        return None
    return format(value, "f")


def _merged_split_record(
    value: Decimal,
    uncertainty: Decimal,
    *,
    evidence: str,
) -> dict[str, Any]:
    return {
        "value": value,
        "uncertainty": uncertainty,
        "method_class": MERGED_SPLIT_METHOD,
        "evidence": evidence,
    }


def _delta_fg_cal(gibbs: Decimal, *, gibbs_page_unit: str) -> Decimal:
    if gibbs_page_unit == "cal/gfw":
        return gibbs
    if gibbs_page_unit == "kcal/gfw":
        return gibbs * Decimal("1000")
    raise ValueError(
        f"ΔfG page unit must be cal/gfw or kcal/gfw, not {gibbs_page_unit!r}"
    )


def _log10k_from_delta_fg_cal(delta_fg_cal: Decimal, temperature: Decimal) -> Decimal:
    """log10 Kf = −ΔfG_cal / (R_CAL T ln 10).

    Premise: B1259 Table 1 prints R = 1.98717 cal deg^-1 gfw^-1. The 298.15 K
    table prints ΔfG in cal gfw^-1; high-T tables print ΔfG in kcal gfw^-1.
    Algebra: log10 Kf = −ΔG_cal / (R T ln 10)
           = −1000 ΔG_kcal / (R T ln 10).
    Unit check: cal / ((cal/(mol·K)) · K) is dimensionless.
    Sanity: Ag+(aq) 298.15 K, ΔfG = 18433 cal → −13.512 vs printed −13.512.
    """

    return -delta_fg_cal / (B1259_R_CAL * temperature * LN10)


def _recover_glued_gibbs_via_logk(
    token: RawToken,
    grains: Mapping[tuple[str, str | None], Decimal],
    t_text: str,
    logk_text: str,
    gibbs_unit: str,
) -> tuple[dict[str, Any], Decimal, Decimal, Decimal] | None:
    """If a jammed integer ΔfG fails 10×, keep the unique grain cut that restores identity."""

    payload = _numeric_payload(token.as_published)
    if not payload or _published_decimal(payload) is None or payload.count(".") >= 2:
        return None
    grain = grains.get((token.column, token.formation_basis))
    if grain is None:
        return None
    passing_1x: list[tuple[Decimal, Decimal, Decimal, Decimal, Decimal]] = []
    passing_notice: list[tuple[Decimal, Decimal, Decimal, Decimal, Decimal]] = []
    for value, uncertainty in _grain_valid_splits(payload, grain, grain):
        checked = _logk_identity(
            t_text, format(value, "f"), logk_text, gibbs_page_unit=gibbs_unit
        )
        if checked is None:
            continue
        residual, tolerance, calculated = checked
        item = (value, uncertainty, residual, tolerance, calculated)
        if residual <= tolerance:
            passing_1x.append(item)
        elif residual <= 10 * tolerance:
            passing_notice.append(item)
    chosen: tuple[Decimal, Decimal, Decimal, Decimal, Decimal] | None = None
    if len(passing_1x) == 1:
        chosen = passing_1x[0]
    elif not passing_1x and len(passing_notice) == 1:
        chosen = passing_notice[0]
    if chosen is None:
        return None
    value, uncertainty, residual, tolerance, calculated = chosen
    reconstruction = _merged_split_record(
        value,
        uncertainty,
        evidence=(
            f"merged value+uncertainty {token.as_published!r} has several grain-"
            f"{grain} cuts; log10_Kf identity uniquely selects {value} ± "
            f"{uncertainty}"
        ),
    )
    return reconstruction, residual, tolerance, calculated


def _logk_identity(
    temperature_token: str,
    gibbs_token: str,
    logk_token: str,
    *,
    gibbs_page_unit: str,
) -> tuple[Decimal, Decimal, Decimal] | None:
    temperature = Decimal(temperature_token)
    if temperature <= 0:
        return None
    gibbs = Decimal(gibbs_token)
    logk = Decimal(logk_token)
    gibbs_cal = _delta_fg_cal(gibbs, gibbs_page_unit=gibbs_page_unit)
    calculated = _log10k_from_delta_fg_cal(gibbs_cal, temperature)
    e_t = _decimal_grain(temperature_token) / 2
    if temperature - e_t <= 0:
        return None
    gibbs_grain = _decimal_grain(gibbs_token)
    gibbs_grain_cal = _delta_fg_cal(gibbs_grain, gibbs_page_unit=gibbs_page_unit)
    denominator = abs(_log10k_from_delta_fg_cal(Decimal("1"), temperature - e_t))
    temperature_term = abs(calculated) * e_t / (temperature - e_t)
    tolerance = (
        _decimal_grain(logk_token) / 2
        + gibbs_grain_cal / 2 * denominator
        + temperature_term
    )
    residual = abs(logk - calculated)
    return residual, tolerance, calculated


def _gibbs_function_identity(
    entropy_token: str,
    h_token: str,
    temperature_token: str,
    gef_token: str,
) -> tuple[Decimal, Decimal, Decimal]:
    """Printed −(G_T−H_298)/T vs S − 1000(H−H298)/T.

    Premise: HT entropy and gibbs_function are cal deg^-1 gfw^-1; H−H298 is
    kcal gfw^-1. The printed gibbs_function column is already negated.
    Algebra: −(G−H298)/T = S − 1000 (H−H298) / T.
    Unit check: cal/K − (cal / K) = cal/K.
    Sanity: Ag 400 K, S=12.010, H=0.625 → 12.010 − 1.5625 = 10.4475 vs
    printed 10.447.
    """

    entropy = Decimal(entropy_token)
    enthalpy = Decimal(h_token)
    temperature = Decimal(temperature_token)
    gef = Decimal(gef_token)
    hht = (Decimal("1000") * enthalpy) / temperature
    calculated = entropy - hht
    enthalpy_term = (Decimal("1000") / temperature) * (_decimal_grain(h_token) / 2)
    temperature_term = abs(hht) * (_decimal_grain(temperature_token) / 2) / temperature
    tolerance = (
        _decimal_grain(entropy_token) / 2
        + _decimal_grain(gef_token) / 2
        + enthalpy_term
        + temperature_term
    )
    residual = abs(gef - calculated)
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
        origin="usgs-b1259-generator",
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
            if token.row_index == row_index
            and token.column == column
            and token.role == "value"
        ),
        None,
    )


def _row_temperature_tokens(tokens: Sequence[RawToken]) -> dict[int, RawToken]:
    result: dict[int, RawToken] = {}
    for token in tokens:
        if token.column == "temperature" and token.row_index is not None:
            if token.role != "value":
                continue
            result[token.row_index] = token
    return result


def _parseable_formula(text: str) -> str | None:
    compact = re.sub(r"\s+", "", text.replace("·", "."))
    if not compact:
        return None
    for candidate in (compact, compact.replace("0", "O")):
        try:
            parse_formula(candidate)
        except Exception:
            continue
        return candidate
    return None


def _formula_and_charge_from_published(
    text: str,
) -> tuple[str | None, int | None]:
    compact = re.sub(r"\s+", "", text).replace("·", ".")
    compact = compact.translate(_SUBSCRIPT_TRANSLATION)
    charge: int | None = None
    quote_tail = re.search(r"[\"']+$", compact)
    if quote_tail is not None:
        charge = -len(quote_tail.group())
        compact = compact[: quote_tail.start()]
    else:
        sign_tail = re.search(r"([+-]+)$", compact)
        if sign_tail is not None:
            signs = sign_tail.group(1)
            if set(signs) == {"+"}:
                charge = len(signs)
            elif set(signs) == {"-"}:
                charge = -len(signs)
            else:
                return None, None
            compact = compact[: sign_tail.start()]
    parsed = _parseable_formula(compact)
    return parsed, charge


def _formula_weight_decimal(text: str | None) -> Decimal | None:
    if not text:
        return None
    return _published_decimal(re.sub(r"\s+", "", text.strip()))


def _gfw_text(record: Mapping[str, Any]) -> str | None:
    cell = _as_published_cell(record.get("gram_formula_weight"))
    if cell is None or not cell[0].strip():
        return None
    return cell[0].strip()


def _name_key(name: str) -> str:
    text = re.sub(r"\([^)]*\)", " ", name)
    words = [word for word in re.split(r"[^A-Za-z]+", text) if word]
    if not words:
        return ""
    return words[0].upper()


def _sibling_name_key(name: str) -> str:
    text = re.sub(r"\([^)]*\)", " ", name or "")
    text = re.sub(r"[^A-Za-z]+", " ", text)
    drop = {"REFERENCE", "STATE", "IDEAL", "GAS", "AQUEOUS", "ION", "STD", "THE"}
    words = [word.upper() for word in text.split() if word.upper() not in drop]
    return " ".join(words)


@lru_cache(maxsize=1)
def _table2_elements() -> tuple[dict[str, Decimal], dict[str, str]]:
    """Table 2 1963 weights, only rows whose name/symbol pair is legible."""

    path = RECORDS_DIR / f"{TABLE2_RECORD_ID}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    corrections: dict[str, str] = {}
    for item in record.get("corrections") or ():
        if not isinstance(item, Mapping):
            continue
        if item.get("field") != "weight":
            continue
        printed = item.get("printed_token")
        quote = str(item.get("image_quote") or "")
        match = re.search(r"\b([A-Z][a-z]?)\b", quote)
        if printed and match:
            corrections[match.group(1)] = str(printed)
    weights: dict[str, Decimal] = {}
    names: dict[str, str] = {}
    for row in record.get("rows") or ():
        if not isinstance(row, Mapping):
            continue
        symbol = str(row.get("symbol_as_published") or "").strip()
        if not re.fullmatch(r"[A-Z][a-z]?", symbol):
            continue
        element = re.sub(r"[^A-Za-z\s]", " ", str(row.get("element_as_published") or ""))
        tokens = [token for token in element.split() if token]
        weight_cell = row.get("weight") if isinstance(row.get("weight"), Mapping) else {}
        mass = _formula_weight_decimal(
            corrections.get(symbol)
            or (weight_cell.get("as_published") if isinstance(weight_cell, Mapping) else None)
        )
        if mass is None:
            continue
        if len(tokens) == 2 and tokens[1] == symbol and len(tokens[0]) >= 3:
            names[tokens[0]] = symbol
            weights[symbol] = mass
        elif len(tokens) == 1 and len(tokens[0]) >= 3 and tokens[0][0].isupper():
            names[tokens[0]] = symbol
            weights[symbol] = mass
    return weights, names


def _parse_counts(formula: str) -> dict[str, Fraction] | None:
    try:
        parsed = parse_formula(formula)
    except Exception:
        if re.fullmatch(r"[A-Z][a-z]?", formula) and formula in _table2_elements()[0]:
            return {formula: Fraction(1)}
        return None
    counts: dict[str, Fraction] = {}
    for element, amount in parsed.elements.items():
        counts[str(element)] = Fraction(str(amount)).limit_denominator(1000)
    return counts or None


def _formula_mass(formula: str) -> Decimal | None:
    counts = _parse_counts(formula)
    if counts is None:
        return None
    weights, _names = _table2_elements()
    if all(element in weights for element in counts):
        total = Decimal("0")
        for element, amount in counts.items():
            mass = weights[element]
            total += mass * Decimal(amount.numerator) / Decimal(amount.denominator)
        return total
    try:
        parsed = parse_formula(formula)
    except Exception:
        return None
    return Decimal(str(parsed.molar_mass_g_mol))


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


def _ocr_formula_variants(text: str) -> tuple[str, ...]:
    """Closed OCR repairs of a printed formula. Mass-check is the gate."""

    compact = re.sub(r"\s+", "", text.replace("·", "."))
    compact = compact.translate(_SUBSCRIPT_TRANSLATION)
    if not compact:
        return ()
    variants = {compact}
    for _step in range(2):
        grown = set(variants)
        for item in variants:
            grown.add(item.replace("°", "O"))
            grown.add(item.replace("0", "O"))
            grown.add(item.replace(",", "2").replace("_", "2"))
            grown.add(item.replace("A1", "Al").replace("C1", "Cl"))
            if item[:1].islower():
                grown.add(item[0].upper() + item[1:])
            if item.endswith("l") and len(item) > 1:
                grown.add(item[:-1] + "I")
        variants = grown
    return tuple(sorted(variants))


def _consulted_formula_reason(
    consulted: Mapping[str, Any], *, prefix: str, extra: str | None = None
) -> str:
    parts = [
        f"formula_as_published={consulted.get('formula_as_published')!r}",
        f"name_as_published={consulted.get('name_as_published')!r}",
        f"phase={consulted.get('phase')!r}",
        f"state_note_as_published={consulted.get('state_note_as_published')!r}",
        f"gram_formula_weight={consulted.get('gram_formula_weight')!r}",
        f"printed_parsed={consulted.get('printed_parsed')!r}",
        f"name_key={consulted.get('name_key')!r}",
        f"name_index={consulted.get('name_index')!r}",
        f"formula_weight_index={consulted.get('formula_weight_index')!r}",
        f"sibling={consulted.get('sibling')!r}",
    ]
    mismatch = consulted.get("printed_mass_mismatch")
    if mismatch:
        parts.append(f"printed_mass_mismatch={mismatch!r}")
    if extra:
        parts.append(extra)
    return f"{prefix} consulted " + ", ".join(parts)


def _load_property_records() -> tuple[Mapping[str, Any], ...]:
    records: list[Mapping[str, Any]] = []
    for path in sorted(RECORDS_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            continue
        kind = payload.get("table_kind")
        if kind in {"properties_298k", "high_temperature"}:
            records.append(payload)
    return tuple(records)


def _consulted_shell(record: Mapping[str, Any]) -> dict[str, Any]:
    raw = record.get("formula_as_published")
    printed_raw = raw.strip() if isinstance(raw, str) and raw.strip() else None
    label = str(record.get("name_as_published") or "")
    return {
        "formula_as_published": printed_raw,
        "name_as_published": label or None,
        "phase": record.get("phase") or None,
        "state_note_as_published": record.get("state_note_as_published") or None,
        "gram_formula_weight": _gfw_text(record),
        "printed_parsed": None,
        "printed_charge": None,
        "name_key": _name_key(label) or None,
        "name_index": None,
        "formula_weight_index": None,
        "sibling": None,
    }


def _resolve_formula_local(record: Mapping[str, Any]) -> FormulaResolution:
    """Printed formula, OCR repair of that formula, or allotrope. No indexes."""

    consulted = _consulted_shell(record)
    printed_raw = consulted["formula_as_published"]
    label = str(consulted["name_as_published"] or "")
    fw_value = _formula_weight_decimal(consulted["gram_formula_weight"])
    if printed_raw:
        parsed, printed_charge = _formula_and_charge_from_published(printed_raw)
        consulted["printed_parsed"] = parsed
        consulted["printed_charge"] = printed_charge
        if parsed is not None:
            return FormulaResolution(
                parsed, "printed_formula", consulted, None, printed_charge
            )
        ocr_hits: list[tuple[str, int | None]] = []
        for variant in _ocr_formula_variants(printed_raw):
            formula, ocr_charge = _formula_and_charge_from_published(variant)
            if formula is None:
                continue
            if fw_value is not None and not _mass_matches_formula_weight(formula, fw_value):
                continue
            ocr_hits.append((formula, ocr_charge))
        unique_ocr = {formula for formula, _charge in ocr_hits}
        if len(unique_ocr) == 1:
            formula = next(iter(unique_ocr))
            charges = {
                item_charge
                for item_formula, item_charge in ocr_hits
                if item_formula == formula
            }
            ocr_charge = next(iter(charges)) if len(charges) == 1 else None
            consulted["printed_ocr"] = formula
            return FormulaResolution(
                formula, "printed_formula_ocr", consulted, None, ocr_charge
            )
    if fw_value is not None:
        named_allotrope = _allotrope_from_weight(label, fw_value)
        if named_allotrope:
            return FormulaResolution(named_allotrope, "formula_weight", consulted, None)
    return FormulaResolution(
        None,
        None,
        consulted,
        _consulted_formula_reason(consulted, prefix=FORMULA_UNRESOLVED_REASON_PREFIX),
    )


@lru_cache(maxsize=1)
def _formula_index_from_298k() -> dict[str, str]:
    formulas: dict[str, set[str]] = defaultdict(set)
    formula_less: dict[str, int] = defaultdict(int)
    for record in _load_property_records():
        if record.get("table_kind") != "properties_298k":
            continue
        name = str(record.get("name_as_published") or "")
        key = _name_key(name)
        if not key:
            continue
        resolved = _resolve_formula_local(record)
        if resolved.formula:
            formulas[key].add(resolved.formula)
        else:
            formula_less[key] += 1
    return {
        key: next(iter(forms))
        for key, forms in formulas.items()
        if len(forms) == 1 and formula_less.get(key, 0) == 0
    }


@lru_cache(maxsize=1)
def _formula_weight_index_from_298k() -> dict[Decimal, str]:
    formulas: dict[Decimal, set[str]] = defaultdict(set)
    for record in _load_property_records():
        if record.get("table_kind") != "properties_298k":
            continue
        resolved = _resolve_formula_local(record)
        gfw = _formula_weight_decimal(_gfw_text(record))
        if (
            resolved.formula
            and gfw is not None
            and _mass_matches_formula_weight(resolved.formula, gfw)
        ):
            formulas[gfw].add(resolved.formula)
    return {
        gfw: next(iter(forms)) for gfw, forms in formulas.items() if len(forms) == 1
    }


@lru_cache(maxsize=1)
def _sibling_formula_index() -> dict[tuple[str, Decimal], str]:
    hits: dict[tuple[str, Decimal], set[str]] = defaultdict(set)
    for record in _load_property_records():
        resolved = _resolve_formula_local(record)
        if not resolved.formula:
            continue
        key = _sibling_name_key(str(record.get("name_as_published") or ""))
        gfw = _formula_weight_decimal(_gfw_text(record))
        if not key or gfw is None:
            continue
        if not _mass_matches_formula_weight(resolved.formula, gfw):
            continue
        hits[(key, gfw)].add(resolved.formula)
    return {item: next(iter(forms)) for item, forms in hits.items() if len(forms) == 1}


def _resolve_formula(record: Mapping[str, Any]) -> FormulaResolution:
    """Page-grounded formula or a refusal. Never title-case a name."""

    local = _resolve_formula_local(record)
    if local.formula and local.source == "printed_formula":
        return local
    consulted = dict(local.consulted)
    label = str(consulted.get("name_as_published") or "")
    key = consulted.get("name_key")
    fw_value = _formula_weight_decimal(consulted.get("gram_formula_weight"))
    indexed = _formula_index_from_298k().get(key) if key else None
    fw_indexed = (
        _formula_weight_index_from_298k().get(fw_value) if fw_value is not None else None
    )
    sibling_key = _sibling_name_key(label)
    sibling = (
        _sibling_formula_index().get((sibling_key, fw_value))
        if sibling_key and fw_value is not None
        else None
    )
    consulted["name_index"] = indexed
    consulted["formula_weight_index"] = None if fw_indexed is None else str(fw_indexed)
    consulted["sibling"] = sibling
    candidates: list[tuple[str, str, int | None]] = []
    if local.formula:
        candidates.append((local.formula, local.source or "local", local.charge))
    if indexed:
        candidates.append((indexed, "name_index", None))
    if fw_indexed:
        candidates.append((fw_indexed, "formula_weight", None))
    if sibling:
        candidates.append((sibling, "sibling", None))
    if fw_value is not None:
        matching = [
            item
            for item in candidates
            if _formula_mass(item[0]) is None
            or _mass_matches_formula_weight(item[0], fw_value)
        ]
        if matching:
            candidates = matching
    unique = {formula for formula, _source, _charge in candidates}
    if len(unique) == 1:
        formula = next(iter(unique))
        sources = sorted({source for value, source, _charge in candidates if value == formula})
        charges = {item_charge for value, _source, item_charge in candidates if value == formula}
        charges.discard(None)
        charge = next(iter(charges)) if len(charges) == 1 else local.charge
        return FormulaResolution(formula, "+".join(sources), consulted, None, charge)
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
    stripped = re.sub(r"\s+", " ", text).strip()
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
        f"name_as_published/phase {text!r} is not a closed Polymorph token"
    )


def _phase_state(record: Mapping[str, Any]) -> tuple[State[Phase], State[Polymorph]]:
    name_text = str(record.get("name_as_published") or "")
    phase_text = str(record.get("phase") or "")
    state_note = str(record.get("state_note_as_published") or "")
    combined = " ".join(part for part in (phase_text, name_text, state_note) if part)
    phase, polymorph = _phase_from_text(combined or name_text)
    if phase.is_value and phase.value is Phase.CR:
        named = _polymorph_from_text(name_text)
        if named.is_value:
            polymorph = named
    return phase, polymorph


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
    if basis != "from_the_elements":
        return None
    for element, amount in counts.items():
        if element in _DIATOMIC_GASES:
            terms.append(ReactionTerm(_element_reference_species(element), -amount / 2))
        else:
            terms.append(ReactionTerm(_element_reference_species(element), -amount))
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


def _pages(record: Mapping[str, Any]) -> tuple[int | None, int | None]:
    printed = record.get("page")
    pdf = record.get("pdf_page")
    printed_page = int(printed) if isinstance(printed, int) else None
    pdf_page = int(pdf) if isinstance(pdf, int) else None
    return printed_page, pdf_page


def _unit_row_locator(kind: str) -> Locator:
    if kind == "properties_298k":
        return Locator(
            pdf_page_index=9,
            published_page=3,
            note=f"{TABLE1_UNIT_LOCATOR}: ΔHf and ΔGf in cal gfw^-1",
        )
    return Locator(
        pdf_page_index=32,
        published_page=26,
        note=f"{HT_UNIT_ROW_LOCATOR}: {HT_UNIT_ROW_QUOTE}",
    )


def _store_value(token: RawToken, value: Decimal) -> Decimal:
    """Convert printed calories to the schema SI unit. as_published stays on the locator."""

    if token.column == "entropy":
        return value * CAL_TH_TO_J
    if token.column == "H_minus_H298":
        return value * CAL_TH_TO_J
    if token.column in {"delta_f_H", "delta_f_G"}:
        if token.table_kind == "properties_298k":
            return value * CAL_TH_TO_KJ
        return value * CAL_TH_TO_J
    return value


def _derivation_parameters(
    token: RawToken, locator: Locator
) -> tuple[tuple[str, Located[Decimal]], ...]:
    parameters: list[tuple[str, Located[Decimal]]] = []
    calorie = Located(State.of(CAL_TH_TO_J), locator=locator)
    if token.column == "entropy":
        parameters.append(("cal_th_to_J", calorie))
    elif token.column == "H_minus_H298":
        parameters.append(("kcal_th_to_kJ", calorie))
    elif token.column in {"delta_f_H", "delta_f_G"}:
        if token.table_kind == "properties_298k":
            parameters.append(
                (
                    "cal_th_to_kJ",
                    Located(State.of(CAL_TH_TO_KJ), locator=locator),
                )
            )
        else:
            parameters.append(("kcal_th_to_kJ", calorie))
    return tuple(parameters)


def _observation(
    *,
    record: Mapping[str, Any],
    token: RawToken,
    quantity: Quantity,
    value: Decimal,
    temperature: Decimal,
    notices: tuple[Notice, ...],
    formula: str,
    charge: int | None,
    uncertainty: Uncertainty | None = None,
    reconstruction: Mapping[str, Any] | None = None,
) -> Observation:
    record_id = str(record["record_id"])
    phase, polymorph = _phase_state(record)
    if (
        phase.is_value
        and phase.value is Phase.AQ
        and charge is None
    ):
        charge_state: State[int] | int | None = State.unknown(
            "formula_as_published has no charge marker; "
            f"consulted formula_as_published={record.get('formula_as_published')!r}, "
            f"name_as_published={record.get('name_as_published')!r}, "
            f"phase={record.get('phase')!r}"
        )
    elif charge is None:
        charge_state = 0
    else:
        charge_state = charge
    species = make_species(formula, phase, polymorph, charge=charge_state)
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
        TABLE1_UNIT_LOCATOR
        if token.table_kind == "properties_298k"
        else HT_UNIT_ROW_LOCATOR
    )
    note = (
        f"as_published={token.as_published!r}; unit={unit!r} quoted from {unit_locator}"
        f"; captured units_as_published ignored; printed value is the evidence "
        f"(calories stay as published; SI is derived with thermochemical calorie "
        f"4.184 J/cal); standard_pressure=1 atm = {STANDARD_PRESSURE_PA} Pa from "
        f"title ({TITLE_PRESSURE_QUOTE})"
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
    relation = f"Direct B1259 tabulation; {role_note}"
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
    published_name = str(record.get("name_as_published") or "") or None
    suffix = tabulated_cell_suffix(
        quantity.value,
        temperature=temperature,
        column=token.column,
        basis=basis or "shared",
        name=published_name,
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
            parameters=_derivation_parameters(token, _unit_row_locator(token.table_kind)),
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
        if token.table_kind != "high_temperature" or token.role != "value":
            continue
        if token.row_index is None:
            continue
        if token.column in {"temperature", "uncertainty", "gram_formula_weight"}:
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


def _identity_band_1x_10x(
    record_id: str,
    identity_results: Sequence[Mapping[str, Any]],
    observations: Sequence[Observation],
) -> list[dict[str, Any]]:
    noticed_ids = {
        observation.observation_id
        for observation in observations
        if observation.notices
    }
    rows: list[dict[str, Any]] = []
    for result in identity_results:
        if result.get("ok") is not False:
            continue
        residual = Decimal(str(result.get("absolute_residual") or "0"))
        tolerance = Decimal(str(result.get("rounding_tolerance") or "0"))
        if not (tolerance > 0 and residual > tolerance and residual <= 10 * tolerance):
            continue
        row_index = result.get("row_index")
        temperature = result.get("temperature_as_published")
        stored_with_notice = sorted(
            observation_id
            for observation_id in noticed_ids
            if temperature is not None and f":T={temperature}:" in observation_id
        )
        rows.append(
            {
                "record_id": record_id,
                "row_index": row_index,
                "temperature_as_published": result.get("temperature_as_published"),
                "identity": result.get("identity"),
                "absolute_residual": result.get("absolute_residual"),
                "rounding_tolerance": result.get("rounding_tolerance"),
                "stored_with_notice": stored_with_notice,
            }
        )
    return rows


def _formula_resolution_report(
    kind: str, resolution: FormulaResolution | None
) -> dict[str, Any]:
    if resolution is None:
        return {"unresolved": False}
    return {
        "formula": resolution.formula,
        "source": resolution.source,
        "consulted": dict(resolution.consulted),
        "reason": resolution.reason,
        "charge": resolution.charge,
        "unresolved": resolution.formula is None,
        "rows_unresolved": 1 if kind == "properties_298k" and resolution.formula is None else 0,
    }


def generate_record(payload: Mapping[str, Any]) -> RecordGeneration:
    """Transform one committed B1259 JSON record into observations and a report."""

    record_id = str(payload.get("record_id") or "")
    if not record_id:
        raise ValueError("B1259 record is missing record_id")
    kind = _table_kind(payload)
    tokens = list(_iter_raw_numeric_tokens(payload))
    grains = _column_grain_map(tokens)
    temperature_by_row = _row_temperature_tokens(tokens)
    usable_t: dict[int, Decimal] = {}
    unusable_t: dict[int, str] = {}
    if kind == "high_temperature":
        for row_index, t_token in temperature_by_row.items():
            value, _recon, reason = _candidate_value(t_token, grains)
            if value is None:
                unusable_t[row_index] = reason or "unusable temperature"
            else:
                usable_t[row_index] = value

    identity_results: list[dict[str, Any]] = []
    identity_fail_10x: set[tuple[int, str]] = set()
    identity_notice: dict[tuple[int, str], tuple[Decimal, Decimal]] = {}
    identity_guarded: set[tuple[int, str]] = set()
    guided_recon: dict[tuple[int, str], dict[str, Any]] = {}
    integer_glue_refuse: dict[tuple[int, str], str] = {}

    if kind in {"high_temperature", "properties_298k"}:
        row_indices = sorted(
            {
                token.row_index
                for token in tokens
                if token.row_index is not None and token.role == "value"
            }
        )
        for row_index in row_indices:
            if kind == "high_temperature":
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
                h_tok = _token_lookup(tokens, row_index, "H_minus_H298")
                gef_tok = _token_lookup(tokens, row_index, "gibbs_function")
                entropy_text = _identity_token_text(entropy_tok, grains)
                h_text = _identity_token_text(h_tok, grains)
                gef_text = _identity_token_text(gef_tok, grains)
                if entropy_text and h_text and gef_text:
                    residual, tolerance, calculated = _gibbs_function_identity(
                        entropy_text, h_text, t_text, gef_text
                    )
                    identity_results.append(
                        {
                            "row_index": row_index,
                            "temperature_as_published": t_text,
                            "identity": "planck_vs_S_minus_1000H_over_T",
                            "printed": gef_tok.as_published if gef_tok else gef_text,
                            "reconstructed_gef": gef_text,
                            "calculated": str(calculated),
                            "absolute_residual": str(residual),
                            "rounding_tolerance": str(tolerance),
                            "ok": residual <= tolerance,
                            "sign_convention": (
                                "printed gibbs_function is -(G_T-H_298)/T; "
                                "compared as printed, not sign-flipped"
                            ),
                        }
                    )
                    if residual > 10 * tolerance:
                        identity_fail_10x.add((row_index, "gibbs_function"))
                        identity_fail_10x.add((row_index, "entropy"))
                        identity_fail_10x.add((row_index, "H_minus_H298"))
                    else:
                        identity_guarded.add((row_index, "gibbs_function"))
                        identity_guarded.add((row_index, "entropy"))
                        identity_guarded.add((row_index, "H_minus_H298"))
                        if residual > tolerance:
                            identity_notice[(row_index, "gibbs_function")] = (
                                residual,
                                tolerance,
                            )
                            identity_notice[(row_index, "entropy")] = (
                                residual,
                                tolerance,
                            )
                            identity_notice[(row_index, "H_minus_H298")] = (
                                residual,
                                tolerance,
                            )
                gibbs_unit = "kcal/gfw"
            else:
                t_text = "298.15"
                gibbs_unit = "cal/gfw"
            dg_tok = _token_lookup(tokens, row_index, "delta_f_G")
            logk_tok = _token_lookup(tokens, row_index, "log_Kf")
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
            recovered_gibbs = None
            if residual > 10 * tolerance and dg_tok is not None:
                recovered = _recover_glued_gibbs_via_logk(
                    dg_tok, grains, t_text, logk_text, gibbs_unit
                )
                if recovered is not None:
                    reconstruction, residual, tolerance, calculated = recovered
                    recovered_gibbs = reconstruction["value"]
                    guided_recon[(row_index, "delta_f_G")] = reconstruction
                    dh_tok = _token_lookup(tokens, row_index, "delta_f_H")
                    if dh_tok is not None:
                        dh_payload = _numeric_payload(dh_tok.as_published)
                        dh_grain = grains.get((dh_tok.column, dh_tok.formation_basis))
                        if (
                            dh_payload
                            and _published_decimal(dh_payload) is not None
                            and dh_payload.count(".") < 2
                            and dh_grain is not None
                            and len(_grain_valid_splits(dh_payload, dh_grain, dh_grain))
                            != 1
                        ):
                            integer_glue_refuse[(row_index, "delta_f_H")] = (
                                INTEGER_GLUE_NO_UNIQUE_SPLIT
                            )
            result = {
                "row_index": row_index,
                "temperature_as_published": t_text,
                "identity": "log10_Kf_from_delta_fG",
                "formation_basis": dg_tok.formation_basis if dg_tok else None,
                "printed": logk_tok.as_published if logk_tok else logk_text,
                "reconstructed_gibbs": dg_text,
                "calculated": str(calculated),
                "absolute_residual": str(residual),
                "rounding_tolerance": str(tolerance),
                "ok": residual <= tolerance,
                "gibbs_page_unit": gibbs_unit,
                "gas_constant_cal_per_mol_K": str(B1259_R_CAL),
            }
            if recovered_gibbs is not None:
                result["reconstructed_gibbs"] = str(recovered_gibbs)
            identity_results.append(result)
            if residual > 10 * tolerance:
                identity_fail_10x.add((row_index, "log_Kf"))
                identity_fail_10x.add((row_index, "delta_f_G"))
                if kind == "properties_298k":
                    identity_fail_10x.add((row_index, "delta_f_H"))
            else:
                identity_guarded.add((row_index, "log_Kf"))
                identity_guarded.add((row_index, "delta_f_G"))
                if residual > tolerance:
                    identity_notice[(row_index, "log_Kf")] = (residual, tolerance)
                    identity_notice[(row_index, "delta_f_G")] = (residual, tolerance)

    record_formula: FormulaResolution | None = None
    if kind in {"properties_298k", "high_temperature"}:
        record_formula = _resolve_formula(payload)

    observations: list[Observation] = []
    refusals: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    vocabulary_gaps: list[dict[str, Any]] = []
    stored = 0
    refused = 0
    excluded = 0
    merged_proposed = 0
    merged_stored = 0
    merged_refused = 0
    merged_refused_10x = 0
    merged_excluded = 0
    unguarded_stored = 0
    unguarded_by_quantity: Counter[str] = Counter()
    pending_merged = False
    by_column: dict[str, Counter[str]] = defaultdict(Counter)
    by_column_basis: dict[str, Counter[str]] = defaultdict(Counter)

    def account(token: RawToken, bucket: str) -> None:
        by_column[token.column][bucket] += 1
        by_column[token.column]["raw"] += 1
        key = f"{token.column}:{token.formation_basis or 'shared'}"
        by_column_basis[key][bucket] += 1
        by_column_basis[key]["raw"] += 1

    def refuse(token: RawToken, reason: str) -> None:
        nonlocal refused, merged_refused, merged_refused_10x, pending_merged
        refused += 1
        if pending_merged:
            merged_refused += 1
            if "10×" in reason:
                merged_refused_10x += 1
            pending_merged = False
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
        nonlocal excluded, merged_excluded, pending_merged
        excluded += 1
        if pending_merged:
            merged_excluded += 1
            pending_merged = False
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
    stored_columns = (
        STORED_HT_COLUMNS if kind == "high_temperature" else STORED_298K_COLUMNS
    )

    for token in tokens:
        pending_merged = False
        stripped = token.as_published.strip()
        if stripped in {"", "-", "—"}:
            exclude(
                token,
                "blank printed cell" if stripped == "" else "printed blank marker",
            )
            continue
        if token.role == "uncertainty":
            exclude(token, UNCERTAINTY_EXCLUDE_REASON)
            continue
        if token.role in {"footer", "reference", "auxiliary"}:
            exclude(
                token,
                "auxiliary printed numeric token (Table 1/2 symbol, footer, "
                "or literature-reference code); not a schema v2.1 quantity",
            )
            continue
        if (
            kind == "high_temperature"
            and token.row_index is not None
            and token.column in stored_columns
            and token.row_index in unusable_t
        ):
            refuse(token, f"row temperature token unusable: {unusable_t[token.row_index]}")
            continue
        glue_key = (
            (token.row_index, token.column) if token.row_index is not None else None
        )
        if glue_key is not None and glue_key in integer_glue_refuse:
            refuse(token, integer_glue_refuse[glue_key])
            continue
        if glue_key is not None and glue_key in guided_recon:
            reconstruction = guided_recon[glue_key]
            value = reconstruction["value"]
            refuse_reason = None
        else:
            value, reconstruction, refuse_reason = _candidate_value(token, grains)
            if refuse_reason:
                refuse(token, refuse_reason)
                continue
        pending_merged = bool(
            reconstruction and reconstruction.get("method_class") == MERGED_SPLIT_METHOD
        )
        if pending_merged:
            merged_proposed += 1
        identity_key = (token.row_index if token.row_index is not None else -1, token.column)
        if identity_key in identity_fail_10x:
            refuse(
                token,
                "identity residual exceeds 10× printed-rounding tolerance; value not stored",
            )
            continue
        if (
            kind == "high_temperature"
            and isinstance(value, Decimal)
            and token.role == "value"
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
        if kind in {"table1_symbols", "table2_weights", "table3_bibliography"}:
            exclude(token, "auxiliary Table 1/2/3 cell; not a schema v2.1 quantity")
            continue
        if token.column not in stored_columns:
            exclude(token, f"column {token.column} is not a stored quantity")
            continue
        if value is None:
            refuse(token, f"unparseable printed token {token.as_published!r}")
            continue
        quantity = COLUMN_QUANTITY[token.column]
        if kind == "properties_298k":
            temperature = REFERENCE_TEMPERATURE_K
        elif token.row_index not in usable_t:
            refuse(token, "row temperature token unusable")
            continue
        else:
            temperature = usable_t[token.row_index]
        if record_formula is None or record_formula.formula is None:
            refuse(
                token,
                (record_formula.reason if record_formula is not None else None)
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
                        "planck_vs_S_minus_1000H_over_T"
                        if token.column
                        in {"gibbs_function", "entropy", "H_minus_H298"}
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
                formula=record_formula.formula,
                charge=record_formula.charge,
                uncertainty=uncertainty,
                reconstruction=reconstruction,
            )
        )
        stored += 1
        if pending_merged:
            merged_stored += 1
            if identity_key not in identity_guarded:
                unguarded_stored += 1
                unguarded_by_quantity[quantity.value] += 1
            pending_merged = False
        account(token, "stored")

    unexplained = len(tokens) - stored - refused - excluded
    if unexplained:
        raise AssertionError(
            f"{record_id}: unexplained numeric tokens: source={len(tokens)} "
            f"stored={stored} refused={refused} excluded={excluded}"
        )
    merged_unexplained = merged_proposed - merged_stored - merged_refused - merged_excluded
    if merged_unexplained:
        raise AssertionError(
            f"{record_id}: merged splits do not close: proposed={merged_proposed} "
            f"stored={merged_stored} refused={merged_refused} excluded={merged_excluded}"
        )
    identity_band_1x_10x = _identity_band_1x_10x(
        record_id, identity_results, observations
    )
    unguarded_computed = sum(unguarded_by_quantity.values())
    if unguarded_computed != unguarded_stored:
        raise AssertionError(
            f"{record_id}: unguarded_stored {unguarded_stored} != "
            f"sum(unguarded_by_quantity) {unguarded_computed}"
        )

    report = {
        "record_id": record_id,
        "table_kind": kind,
        "formula": payload.get("formula_as_published"),
        "formation_basis": (
            "from_the_elements"
            if kind in {"properties_298k", "high_temperature"}
            else None
        ),
        "gas_constant_cal_per_mol_K": str(B1259_R_CAL),
        "gas_constant_J_from_cal": str(B1259_R_J_FROM_CAL),
        "gas_constant_J_printed_companion": str(B1259_R_J_PRINTED),
        "gas_constant_basis": B1259_R_SOURCE,
        "unit_row": {
            "ht_quote": HT_UNIT_ROW_QUOTE,
            "ht_locator": HT_UNIT_ROW_LOCATOR,
            "table1_locator": TABLE1_UNIT_LOCATOR,
            "table_298k_formation_unit": PAGE_UNITS_298K["delta_f_H"],
            "ht_formation_unit": PAGE_UNITS_HT["delta_f_H"],
            "thermochemical_calorie_J": str(CAL_TH_TO_J),
        },
        "standard_pressure": {
            "quote": TITLE_PRESSURE_QUOTE,
            "standard_pressure_Pa": str(STANDARD_PRESSURE_PA),
            "standard_atmosphere_Pa": str(STANDARD_ATMOSPHERE_PA),
        },
        "gibbs_function_sign": {
            "printed_column": "-(G_T-H_298)/T",
            "identity": "planck_vs_S_minus_1000H_over_T",
            "reason": GIBBS_FUNCTION_EXCLUDE_REASON,
        },
        "neighbour_sign": {
            "applied": kind == "high_temperature",
            "disabled_on_298k": kind == "properties_298k",
            "disabled_on_table1_table2": kind
            in {"table1_symbols", "table2_weights", "table3_bibliography"},
            "reason": NEIGHBOUR_SIGN_DISABLED_REASON,
        },
        "merged_cell_splits": {
            "proposed": merged_proposed,
            "stored": merged_stored,
            "refused": merged_refused,
            "refused_10x": merged_refused_10x,
            "excluded": merged_excluded,
            "unguarded_stored": unguarded_stored,
            "unguarded_by_quantity": dict(sorted(unguarded_by_quantity.items())),
            "unguarded_reason": UNGUARDED_MERGED_REASON,
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
        "identity_notice_1x_10x": identity_band_1x_10x,
        "neighbour_sign_hits": neighbour_hits,
        "formula_resolution": _formula_resolution_report(kind, record_formula),
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
        raise ValueError(f"{directory}: no B1259 JSON records")
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
    merged_proposed_total = 0
    merged_stored_total = 0
    merged_refused_total = 0
    merged_refused_10x_total = 0
    merged_excluded_total = 0
    unguarded_stored_total = 0
    unguarded_by_quantity: Counter[str] = Counter()
    identity_fail_counts: Counter[str] = Counter()
    identity_fail_10x = 0
    identity_band_1x_10x: list[object] = []
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
        merged_proposed_total += int(merged["proposed"])
        merged_stored_total += int(merged["stored"])
        merged_refused_total += int(merged["refused"])
        merged_refused_10x_total += int(merged["refused_10x"])
        merged_excluded_total += int(merged["excluded"])
        unguarded_stored_total += int(merged["unguarded_stored"])
        for quantity, count in (merged.get("unguarded_by_quantity") or {}).items():
            unguarded_by_quantity[str(quantity)] += int(count)
        for result in generated.report["identity_results"]:
            if result.get("ok") is False:
                identity_fail_counts[str(result.get("identity"))] += 1
                residual = Decimal(str(result.get("absolute_residual") or "0"))
                tolerance = Decimal(str(result.get("rounding_tolerance") or "0"))
                if tolerance > 0 and residual > 10 * tolerance:
                    identity_fail_10x += 1
        identity_band_1x_10x.extend(generated.report.get("identity_notice_1x_10x") or [])
        now = time.monotonic()
        resolution = generated.report.get("formula_resolution") or {}
        if generated.report["table_kind"] == "properties_298k":
            formula_unresolved_rows += int(resolution.get("rows_unresolved") or 0)
        elif resolution.get("unresolved"):
            formula_unresolved_records += 1
        if now - last_progress >= 20 or record_count % 50 == 0:
            print(
                f"B1259 generator: {record_count} records in {now - started:.1f}s "
                f"stored={stored_total} refused={refused_total} excluded={excluded_total}",
                flush=True,
            )
            last_progress = now
    if record_count == 0:
        raise ValueError("no B1259 documents supplied")
    unexplained = raw_total - stored_total - refused_total - excluded_total
    if unexplained:
        raise AssertionError(
            f"B1259 census does not close: raw={raw_total} stored={stored_total} "
            f"refused={refused_total} excluded={excluded_total} unexplained={unexplained}"
        )
    dump_yaml(
        {
            "schema_version": "battery_observations.v2.1",
            "source_id": SOURCE_ID,
            "observations": observations,
        },
        out / "observations" / "usgs-b1259.yaml",
    )
    dump_yaml(
        {
            "schema_version": "usgs_b1259_generator_report.v1",
            "source_id": SOURCE_ID,
            "records": reports,
        },
        out / "reports" / "usgs-b1259.yaml",
    )
    summary = {
        "schema_version": "usgs_b1259_generator_summary.v1",
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
            "proposed": merged_proposed_total,
            "stored": merged_stored_total,
            "refused": merged_refused_total,
            "refused_10x": merged_refused_10x_total,
            "excluded": merged_excluded_total,
            "unguarded_stored": unguarded_stored_total,
            "unguarded_by_quantity": dict(sorted(unguarded_by_quantity.items())),
            "unguarded_reason": UNGUARDED_MERGED_REASON,
        },
        "identity_failure_counts": dict(sorted(identity_fail_counts.items())),
        "identity_fail_10x": identity_fail_10x,
        "identity_notice_1x_10x": identity_band_1x_10x,
        "identity_notice_1x_10x_count": len(identity_band_1x_10x),
        "notice_count": notice_count,
        "formula_unresolved_ht_records": formula_unresolved_records,
        "formula_unresolved_298k_rows": formula_unresolved_rows,
        "gas_constant_cal_per_mol_K": str(B1259_R_CAL),
        "gas_constant_basis": B1259_R_SOURCE,
        "neighbour_sign_disabled_on": "properties_298k",
        "neighbour_sign_disabled_reason": NEIGHBOUR_SIGN_DISABLED_REASON,
    }
    dump_yaml(summary, out / "summary.yaml")
    print(f"B1259 generator: wrote {record_count} records to {out}", flush=True)
    return summary


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--records",
        type=Path,
        default=RECORDS_DIR,
        help="directory of committed B1259 JSON records",
    )
    parser.add_argument("--out", type=Path, required=True, help="empty staging directory")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    write_staging(documents_from_records(args.records), args.out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
