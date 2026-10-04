"""Generate schema-v2.1 observations from NIST-JANAF table payloads."""

from __future__ import annotations

import argparse
import hashlib
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from simulator.battery.enums import (
    QUANTITY_UNITS,
    AdmissionStatus,
    Authority,
    EvidenceClass,
    NoticeKind,
    PerBasis,
    Phase,
    Polymorph,
    Quantity,
    StateTag,
    UncertaintyKind,
    ValueKind,
)
from simulator.yaml_cache import load_cached_safe_yaml
from simulator.battery.identity import log10K_from_delta_fG_kJ_mol
from simulator.battery.migrate import dump_yaml, fill_identity, make_species, to_plain
from simulator.battery.polymorph_dictionary import (
    JANAF_LABEL_POLYMORPHS,
    JANAF_TRANSITION_POLYMORPHS,
    canonicalize_janaf_transition,
    resolve_janaf_polymorph,
)
from simulator.battery.stable_ids import (
    phase_window_suffix,
    temperature_token as format_temperature_token,
)
from simulator.battery.records import (
    Admission,
    Derivation,
    Evidence,
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
from simulator.reference_data.janaf import (
    COMPILATION_ROLE,
    ELEMENT_SYMBOLS,
    GRID_ORDER_REASON,
    GRID_RANGE_REASON,
    NIST_TAIL_DFH_ABSENCE_REPAIR,
    NIST_TAIL_PARSE_REPAIR,
    NON_DATA_MARKER_KIND,
    NON_DATA_MARKER_REASON,
    REFUSED_LAYOUT_KIND,
    SIDECAR_PATH,
    TABLES_DIR,
    formula_composition,
    _is_nist_typed_absent_enthalpy_cell,
    load_table_document,
    table_printed_temperatures,
)
from tools.harvest_janaf_compilation import parse_table

SOURCE_ID = "nist-janaf-4th"
TABLE_SOURCE_PREFIX = "data/literature/compilations/janaf/tables"
STANDARD_STATE = "p° = 0.1 MPa (as published by JANAF)"
STANDARD_PRESSURE_PA = Decimal("100000")
# Chase 1998, NIST-JANAF 4th ed., Monograph 9, printed p. 10 §3.4
# "Fundamental constants" table: molar gas constant R = 8.314 41(26)
# J mol^-1 K^-1 (Cohen & Taylor 1973 / CODATA 1973). The same page's
# right-hand column notes that tables issued since the Third Edition
# use 1986 CODATA; the printed constants table, and the log Kf column
# identity against it, keep 8.31441. Do not substitute CODATA 2018.
JANAF_R_J_PER_MOL_K = Decimal("8.31441")
MERGED_FORMATION_REFUSAL_REASON = (
    "NIST's rendering drops minus signs in space-joined formation fields; "
    "a nonzero printed token is sign-ambiguous"
)
PRINTED_CELL_ERRATUM_REASON = (
    "printed cell is out of line with both neighbouring rows and violates the "
    "Gibbs-function identity"
)
# B-133 at 300 K is the source disagreeing with itself, not a parse
# defect and not a tolerance miss. The printed 300 K log Kf token is
# the 298.15 K value copied down. Identity: log10 Kf =
# -1000*delta_fG/(R*T*ln(10)) with R = 8.31441 J mol^-1 K^-1 gives
# 5516.922 / (0.00831441 * 300 * ln(10)) ≈ 960.56 against printed
# 966.926 (residual ≈ 6.4 vs rounding tolerance ≈ 1.6, about 4×, not
# 10×). Do not widen the tolerance to absorb it.
SOURCE_DISAGREEMENTS = (
    {
        "table_id": "B-133",
        "temperature_as_published": "300",
        "identity": "log10_Kf_from_delta_fG",
        "kind": "source_disagreement",
        "reason": (
            "printed table repeats the 298.15 K log Kf token at the 300 K "
            "row; not a parse error"
        ),
        "quoted_evidence": {
            "298.15": {
                "delta_fG": "-5519.114",
                "log10_Kf": "966.926",
                "negative_gibbs_enthalpy_function": "324.085",
            },
            "300": {
                "delta_fG": "-5516.922",
                "log10_Kf": "966.926",
                "negative_gibbs_enthalpy_function": "324.085",
            },
        },
    },
)
STORED_CELL_ERRATA = (
    {
        "table_id": "Hf-004",
        "temperature_as_published": "2500.000",
        "line_number": 35,
        "column": "enthalpy_increment",
        "quoted_evidence": (
            "2500.000\t37.656\t125.068\t99.746\t66.307\tBETA <--> LIQUID"
        ),
        "neighbouring_rows": (
            "2500\t37.656\t125.068\t99.746\t63.307\t29.288\t0.\t0.",
            "2600\t37.656\t126.545\t100.748\t67.072\t0. 0. 0.",
        ),
    },
)
CONCATENATED_ROW_REFUSAL_REASON = (
    "concatenated temperature/Cp recovery requires a labelled transition "
    "temperature with exactly three decimals and an immediately following "
    "TRANSITION row with the same temperature"
)
# A rounding-only residual is O(1)×tolerance. A decade scale error or a
# dropped minus produces residuals ≫ 10×tolerance (Al-006 298.15 log Kf
# ×10: residual/tolerance ≈ 4.4e5; sign flip ≈ 9.7e4). B-133 at 300 K is
# a source-copy residual ≈ 4×tolerance and must still store.
SCALE_ERROR_FACTOR = Decimal("10")
SCALE_ERROR_REFUSAL_REASON = (
    "log10_Kf disagrees with delta_fG by more than ten times the "
    "printed-rounding tolerance"
)
# Neighbour-sign reports a value whose sign is opposite both nearest
# nonzero neighbours in the same segment. It is advisory-only: I-013 at
# 5000 K is a real reference-state zero-crossing, not a dropped minus.
# Dropped-minus on a (delta_fG, log10_Kf) pair is refused by the 10×
# stored-pair identity check, not by this scan.
NEIGHBOUR_SIGN_SCAN_STATUS = "advisory-only"
NEIGHBOUR_SIGN_SCAN_CATCHES = (
    "dropped-minus on a stored (delta_fG, log10_Kf) pair is refused by "
    "SCALE_ERROR_REFUSAL_REASON (residual > 10× printed-rounding tolerance)"
)
# Gibbs-function identity reports and does not refuse S or H-H(Tr). phi is
# never stored. Corpus 10× Gibbs residuals (B-133 600 K; Hf-004 2500 K,
# already a named enthalpy erratum) stay disclosed, not a store refusal.
GIBBS_IDENTITY_SCAN_STATUS = "advisory-only"
CIRCULARITY_WARNING = "Do not validate an engine against a compilation it consumes."
# Printed GLASS <--> LIQUID (or LIQ) on a liquid table. Not a measured Tg.
_GLASS_REGION_LABEL = "supercooled liquid / glass-transition region"

_UNITS = {
    "temperature": "K",
    "heat_capacity": "J K^-1 mol^-1",
    "entropy": "J K^-1 mol^-1",
    "negative_gibbs_enthalpy_function": "J K^-1 mol^-1",
    "enthalpy_increment": "kJ mol^-1",
    "formation_enthalpy": "kJ mol^-1",
    "formation_gibbs_energy": "kJ mol^-1",
    "log10_formation_equilibrium_constant": "dimensionless",
}
_COLUMN_QUANTITIES = {
    "heat_capacity": Quantity.CP,
    "entropy": Quantity.S,
    "enthalpy_increment": Quantity.H_MINUS_H298,
    "formation_enthalpy": Quantity.DELTA_FH,
    "formation_gibbs_energy": Quantity.DELTA_FG,
    "log10_formation_equilibrium_constant": Quantity.LOG10_KF,
}
_SHORT_ROW_COLUMNS = (
    "temperature",
    "heat_capacity",
    "entropy",
    "negative_gibbs_enthalpy_function",
    "enthalpy_increment",
)
_FORMATION_TAIL_COLUMNS = (
    "formation_enthalpy",
    "formation_gibbs_energy",
    "log10_formation_equilibrium_constant",
)
_FORMATION_PAIR_COLUMNS = (
    "formation_gibbs_energy",
    "log10_formation_equilibrium_constant",
)
_SINGLE_PHASES = {"g": Phase.G, "cr": Phase.CR, "l": Phase.L}
_COMBINED_STATES = frozenset({"cr,l", "ref", "l,g"})
# If a held JANAF (ref) table for one of these exact formulas has neither a
# phase in its heading nor transition rows, use the monograph's ideal-gas
# convention and mark every dependent observation because that page is not held.
JANAF_REFERENCE_PHASE_BY_CONVENTION = frozenset(
    {"O2", "H2", "N2", "F2", "Cl2", "He", "Ne", "Ar", "Kr", "Xe", "Rn"}
)
_FIXED_REFERENCE_PHASES = {
    "C": Phase.CR,
    **{formula: Phase.G for formula in JANAF_REFERENCE_PHASE_BY_CONVENTION},
}
_PHASE_SIDE = {
    "CRYSTAL": Phase.CR,
    "LIQUID": Phase.L,
    "LIQ": Phase.L,
    "GLASS": Phase.GLASS,
    "GAS": Phase.G,
    "IDEAL GAS": Phase.G,
    "REAL GAS": Phase.G,
}
_CRYSTAL_POLYMORPHS = JANAF_TRANSITION_POLYMORPHS
_PHASE_CHANGE_RE = re.compile(r"^\s*(.+?)\s*<-->\s*(.+?)\s*$")
_CONDITION_RE = re.compile(
    r"^\s*(FUGACITY|PRESSURE)\s*=\s*"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)\s+bar\s*$",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?$")
_CONCATENATED_TEMPERATURE_CP_RE = re.compile(
    r"^([+-]?\d+\.\d{3})"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)$"
)
_CONCATENATED_NUMERIC_FIELD_RE = re.compile(r"^[0-9Ee+.-]+$")


@dataclass(frozen=True)
class TableGeneration:
    observations: tuple[Observation, ...]
    report: Mapping[str, Any]


@dataclass(frozen=True)
class _Cell:
    token: str
    value: Decimal | None


@dataclass(frozen=True)
class _Row:
    temperature: Decimal
    temperature_token: str
    cells: Mapping[str, _Cell]
    order: int
    line_number: int | None = None
    label: str | None = None
    numeric_tail: Mapping[str, _Cell] | None = None
    tail_text: str | None = None
    is_short: bool = False
    raw_line: str | None = None
    recovered_concatenated_temperature_cp: bool = False


@dataclass(frozen=True)
class _Boundary:
    temperature: Decimal
    label: str
    left: str
    right: str
    row_order: int


@dataclass(frozen=True)
class _Segment:
    index: int
    phase: State[Phase]
    polymorph: State[Polymorph]
    phase_basis: str
    lower_K: Decimal | None
    upper_K: Decimal | None
    boundary_labels: tuple[str, ...]


@dataclass(frozen=True)
class _ElementReferenceSchedule:
    table_id: str
    formula: str
    lower_K: Decimal
    upper_K: Decimal
    boundaries: tuple[_Boundary, ...]
    segments: tuple[_Segment, ...]
    heading_phase: Phase | None


@dataclass(frozen=True)
class _FormationBasis:
    reaction: State[Reaction]
    formation_elements: State[tuple[tuple[str, Species], ...]]
    reference_locator_key: tuple[tuple[str, str, int | None, int | None], ...]
    locator_note: str
    convention_tables: tuple[tuple[str, str], ...] = ()

    @property
    def identity_suffix(self) -> str:
        parts = []
        for element, table_id, lower_line, upper_line in self.reference_locator_key:
            if table_id == "missing":
                parts.append(f"{element}-no-ref")
            elif lower_line == -1:
                parts.append(f"{table_id}-below-range")
            elif lower_line == -2:
                parts.append(f"{table_id}-above-range")
            elif lower_line is None and upper_line is None:
                parts.append(f"{table_id}-no-transition")
            elif lower_line is not None and lower_line == upper_line:
                parts.append(f"{table_id}-at-{lower_line}")
            elif lower_line is not None and upper_line is None:
                parts.append(f"{table_id}-after-{lower_line}")
            elif lower_line is None and upper_line is not None:
                parts.append(f"{table_id}-before-{upper_line}")
            else:
                parts.append(f"{table_id}-{lower_line}-{upper_line}")
        return "formation-ref-" + "-".join(parts)


def _published_decimal(token: object) -> Decimal | None:
    text = "" if token is None else str(token).strip()
    if not text or text.upper() == "INFINITE":
        return None
    if not _NUMBER_RE.fullmatch(text):
        raise ValueError(f"non-numeric JANAF cell token {text!r}")
    try:
        return Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - regex already guards this
        raise ValueError(f"invalid JANAF decimal token {text!r}") from exc


def _cell(cell: object, *, table_id: str, column: str) -> _Cell:
    if not isinstance(cell, Mapping) or "as_published" not in cell:
        raise ValueError(f"{table_id}: {column} cell lacks as_published")
    token = "" if cell.get("as_published") is None else str(cell["as_published"])
    value = _published_decimal(token)
    parsed = cell.get("value")
    locator = cell.get("locator")
    typed_absence = (
        isinstance(locator, Mapping)
        and locator.get("parse_repair") == NIST_TAIL_DFH_ABSENCE_REPAIR
    )
    repaired = (
        isinstance(locator, Mapping)
        and locator.get("parse_repair") == NIST_TAIL_PARSE_REPAIR
        and bool(locator.get("raw_line"))
    )
    if typed_absence:
        if column != "formation_enthalpy" or not _is_nist_typed_absent_enthalpy_cell(cell):
            raise ValueError(f"{table_id}: {column} has an inconsistent typed JANAF absence")
        return _Cell(token=token, value=None)
    if value is None:
        if parsed is not None:
            raise ValueError(
                f"{table_id}: {column} token {token!r} is null but value is {parsed!r}"
            )
    elif repaired:
        restored = Decimal(str(parsed)) if parsed is not None else None
        if restored is None or abs(restored) != abs(value):
            raise ValueError(
                f"{table_id}: {column} repaired token/value mismatch: "
                f"{token!r} != {parsed!r}"
            )
        value = restored
    elif parsed is None or Decimal(str(parsed)) != value:
        raise ValueError(
            f"{table_id}: {column} token/value mismatch: {token!r} != {parsed!r}"
        )
    return _Cell(token=token, value=value)


def _unwrap_table(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    table = payload.get("table")
    return table if isinstance(table, Mapping) else payload


def _validate_table(table: Mapping[str, Any]) -> tuple[str, str, int, str]:
    table_id = str(table.get("table_id") or "")
    if not table_id:
        raise ValueError("JANAF table is missing table_id")
    if str(table.get("standard_state_as_published") or "") != STANDARD_STATE:
        raise ValueError(
            f"{table_id}: expected standard state {STANDARD_STATE!r}, got "
            f"{table.get('standard_state_as_published')!r}"
        )
    units = table.get("units_as_published")
    if not isinstance(units, Mapping):
        raise ValueError(f"{table_id}: missing units_as_published")
    for column, expected in _UNITS.items():
        if units.get(column) != expected:
            raise ValueError(
                f"{table_id}: {column} unit {units.get(column)!r} != {expected!r}"
            )
    entry = table.get("index_entry")
    if not isinstance(entry, Mapping):
        raise ValueError(f"{table_id}: missing index_entry")
    formula = str(entry.get("formula_normalised") or "")
    parsed_formula = formula_composition(formula)
    if parsed_formula is None or any(element not in ELEMENT_SYMBOLS for element, _ in parsed_formula):
        raise ValueError(f"{table_id}: formula_normalised fails closed element parse: {formula!r}")
    try:
        charge = int(entry.get("charge", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{table_id}: invalid charge {entry.get('charge')!r}") from exc
    state = str(entry.get("state") or "")
    if state not in {*_SINGLE_PHASES, "fl", *_COMBINED_STATES}:
        raise ValueError(f"{table_id}: unsupported JANAF state {state!r}")
    return table_id, formula, charge, state


def _structured_rows(table: Mapping[str, Any], table_id: str) -> list[_Row]:
    raw_rows = table.get("values")
    if not isinstance(raw_rows, list):
        raise ValueError(f"{table_id}: values must be a list")
    ambiguity_lines = {
        int(item["line_number"])
        for item in table.get("parse_ambiguities") or ()
        if isinstance(item, Mapping)
        and isinstance(item.get("line_number"), int)
        and item.get("raw_line")
    }
    rows: list[_Row] = []
    line_number = 3
    for index, raw in enumerate(raw_rows):
        while line_number in ambiguity_lines:
            line_number += 1
        if not isinstance(raw, Mapping):
            raise ValueError(f"{table_id}: values[{index}] is not a mapping")
        gibbs_cell = raw.get("formation_gibbs_energy")
        gibbs_locator = (
            gibbs_cell.get("locator") if isinstance(gibbs_cell, Mapping) else None
        )
        repaired_line = (
            gibbs_locator.get("line_number")
            if isinstance(gibbs_locator, Mapping)
            and gibbs_locator.get("parse_repair") == NIST_TAIL_PARSE_REPAIR
            else None
        )
        if isinstance(repaired_line, int):
            line_number = repaired_line
        cells = {
            column: _cell(raw.get(column), table_id=table_id, column=column)
            for column in _UNITS
        }
        temperature = cells["temperature"]
        if temperature.value is None:
            raise ValueError(f"{table_id}: values[{index}] has null temperature")
        rows.append(
            _Row(
                temperature=temperature.value,
                temperature_token=temperature.token,
                cells=cells,
                order=line_number,
                line_number=line_number,
            )
        )
        line_number += 1
    return rows


def _trimmed_tab_fields(line: str) -> list[str]:
    fields = line.split("\t")
    while fields and fields[-1] == "":
        fields.pop()
    return fields


def _raw_numeric_token_count(line: str) -> int:
    count = 0
    fields = _trimmed_tab_fields(line)
    for field in fields:
        tokens = field.split()
        if tokens and all(_NUMBER_RE.fullmatch(token) for token in tokens):
            count += len(tokens)
        elif (
            not any(character.isspace() for character in field)
            and field.count(".") >= 2
            and _CONCATENATED_NUMERIC_FIELD_RE.fullmatch(field)
        ):
            count += 2
    first_field_has_temperature = bool(fields) and (
        _NUMBER_RE.fullmatch(fields[0]) is not None
        or (
            fields[0].count(".") >= 2
            and _CONCATENATED_NUMERIC_FIELD_RE.fullmatch(fields[0]) is not None
        )
    )
    return count - int(first_field_has_temperature)


def _short_rows(
    table: Mapping[str, Any], table_id: str
) -> tuple[list[_Row], list[dict[str, Any]]]:
    rows: list[_Row] = []
    ambiguities = table.get("parse_ambiguities") or []
    if not isinstance(ambiguities, list):
        raise ValueError(f"{table_id}: parse_ambiguities must be a list")
    normalized_fields: dict[int, list[str]] = {}
    recovered_indices: set[int] = set()
    refused_indices: set[int] = set()
    refused_rows: list[dict[str, Any]] = []
    for ambiguity_index, item in enumerate(ambiguities):
        if ambiguity_index in refused_indices:
            continue
        if not isinstance(item, Mapping) or not item.get("raw_line"):
            continue
        if item.get("kind") in {NON_DATA_MARKER_KIND, REFUSED_LAYOUT_KIND}:
            continue
        fields = _trimmed_tab_fields(str(item["raw_line"]))
        if (
            len(fields) != 5
            or fields[0].count(".") < 2
            or _PHASE_CHANGE_RE.fullmatch(fields[-1].strip()) is None
        ):
            continue
        transition_match = _CONCATENATED_TEMPERATURE_CP_RE.fullmatch(
            fields[0].strip()
        )
        companion_index = ambiguity_index + 1
        companion = (
            ambiguities[companion_index]
            if companion_index < len(ambiguities)
            and isinstance(ambiguities[companion_index], Mapping)
            else None
        )
        companion_fields = (
            _trimmed_tab_fields(str(companion.get("raw_line") or ""))
            if companion is not None
            else []
        )
        companion_is_next_line = (
            isinstance(item.get("line_number"), int)
            and companion is not None
            and companion.get("line_number") == item["line_number"] + 1
        )
        companion_is_transition = (
            bool(companion_fields)
            and companion_fields[-1].strip().upper() == "TRANSITION"
        )
        companion_match = (
            _CONCATENATED_TEMPERATURE_CP_RE.fullmatch(
                companion_fields[0].strip()
            )
            if len(companion_fields) == 5
            else None
        )
        companion_temperature = (
            companion_match.group(1) if companion_match is not None else None
        )
        fields_are_numeric = all(_NUMBER_RE.fullmatch(field) for field in fields[1:4])
        companion_fields_are_numeric = companion_match is not None and all(
            _NUMBER_RE.fullmatch(field) for field in companion_fields[1:4]
        )
        valid_pair = (
            transition_match is not None
            and companion_is_next_line
            and companion_is_transition
            and companion_temperature == transition_match.group(1)
            and fields_are_numeric
            and companion_fields_are_numeric
        )
        paired_indices = [ambiguity_index]
        if companion_is_next_line and companion_is_transition:
            paired_indices.append(companion_index)
        if not valid_pair:
            for refused_index in paired_indices:
                refused_item = ambiguities[refused_index]
                refused_indices.add(refused_index)
                refused_rows.append(
                    {
                        "line_number": refused_item.get("line_number"),
                        "raw_text": str(refused_item.get("raw_line") or ""),
                        "reason": CONCATENATED_ROW_REFUSAL_REASON,
                    }
                )
            continue
        normalized_fields[ambiguity_index] = [
            transition_match.group(1),
            transition_match.group(2),
            *fields[1:],
        ]
        recovered_indices.add(ambiguity_index)
        if companion_match is not None:
            normalized_fields[companion_index] = [
                companion_match.group(1),
                companion_match.group(2),
                *companion_fields[1:],
            ]
            recovered_indices.add(companion_index)

    for ambiguity_index, item in enumerate(ambiguities):
        if ambiguity_index in refused_indices:
            continue
        if not isinstance(item, Mapping) or not item.get("raw_line"):
            continue
        if item.get("kind") in {NON_DATA_MARKER_KIND, REFUSED_LAYOUT_KIND}:
            continue
        line = str(item["raw_line"])
        fields = normalized_fields.get(ambiguity_index, _trimmed_tab_fields(line))
        if len(fields) != 6:
            continue
        try:
            short_cells = {
                column: _Cell(str(token), _published_decimal(token))
                for column, token in zip(_SHORT_ROW_COLUMNS, fields[:5], strict=True)
            }
        except ValueError:
            continue
        temperature = short_cells["temperature"]
        if temperature.value is None:
            continue
        tail_tokens = fields[5].split()
        numeric_tail: dict[str, _Cell] | None = None
        label: str | None = fields[5].strip()
        if len(tail_tokens) == 3 and all(
            _NUMBER_RE.fullmatch(token) for token in tail_tokens
        ):
            try:
                numeric_tail = {
                    column: _Cell(token, _published_decimal(token))
                    for column, token in zip(
                        _FORMATION_TAIL_COLUMNS, tail_tokens, strict=True
                    )
                }
            except ValueError:
                numeric_tail = None
            else:
                label = None
        line_number = item.get("line_number")
        rows.append(
            _Row(
                temperature=temperature.value,
                temperature_token=temperature.token,
                cells=short_cells,
                order=int(line_number) if isinstance(line_number, int) else 1_000_000 + ambiguity_index,
                line_number=int(line_number) if isinstance(line_number, int) else None,
                label=label,
                numeric_tail=numeric_tail,
                tail_text=fields[5],
                is_short=True,
                raw_line=line,
                recovered_concatenated_temperature_cp=(
                    ambiguity_index in recovered_indices
                ),
            )
        )
    return rows, refused_rows


def _glass_liquid_markers(rows: Sequence[_Row]) -> tuple[_Boundary, ...]:
    """Printed glass/liquid labels. Other spans are not boundaries."""

    found: list[_Boundary] = []
    for row in rows:
        parts = _phase_change(row.label)
        if parts is None:
            continue
        left, right = parts
        if {_side_phase(left), _side_phase(right)} == {Phase.GLASS, Phase.L}:
            found.append(
                _Boundary(row.temperature, row.label or "", left, right, row.order)
            )
    return tuple(found)


def _on_printed_left(row: _Row, marker: _Boundary) -> bool:
    """Same-temperature rule as segment assignment: the labelled row stays left."""

    later_short = (
        marker.temperature == row.temperature
        and row.is_short
        and marker.row_order < row.order
    )
    return marker.temperature > row.temperature or (
        marker.temperature == row.temperature and not later_short
    )


def _on_glass_side(row: _Row, marker: _Boundary) -> bool:
    on_left = _on_printed_left(row, marker)
    if _side_phase(marker.left) is Phase.GLASS:
        return on_left
    return not on_left


def _row_stores_number(
    row: _Row, table_id: str, scale_error_orders: set[int]
) -> bool:
    for column in _COLUMN_QUANTITIES:
        cell = (
            row.numeric_tail.get(column)
            if row.numeric_tail is not None and column in _FORMATION_TAIL_COLUMNS
            else row.cells.get(column)
        )
        if cell is None or cell.value is None:
            continue
        if _printed_cell_erratum(table_id, row, column) is not None:
            continue
        if (
            row.numeric_tail is not None
            and column in _FORMATION_TAIL_COLUMNS
            and cell.value != 0
        ):
            continue
        if column in _FORMATION_PAIR_COLUMNS and row.order in scale_error_orders:
            continue
        return True
    return False


def _glass_region_segment(
    segment: _Segment,
    marker: _Boundary,
    rows: Sequence[_Row],
    *,
    table_id: str,
    scale_error_orders: set[int],
    temperature_token: str,
) -> _Segment:
    """Relabel a liquid series that stores glass-side rows.

    One observation per series stays one observation. A series that stores
    both sides is not stamped liquid: species.phase holds one phase.
    """

    glass = False
    liquid = False
    for row in rows:
        if not _row_stores_number(row, table_id, scale_error_orders):
            continue
        if _on_glass_side(row, marker):
            glass = True
        else:
            liquid = True
        if glass and liquid:
            break
    if not glass:
        return segment
    if liquid:
        reason = (
            f'printed "{marker.label}" at {temperature_token} K; rows through '
            f"that labelled transition are the {_GLASS_REGION_LABEL} and "
            "following rows are liquid; v2.1 species.phase has one phase axis "
            "and cannot hold both, so this series is not stamped liquid"
        )
        phase: State[Phase] = State.unknown(reason)
        basis = reason
    else:
        basis = (
            f'printed "{marker.label}" at {temperature_token} K; every stored '
            f"row is on the glass side ({_GLASS_REGION_LABEL})"
        )
        phase = State.of(Phase.GLASS)
    return _Segment(
        segment.index,
        phase,
        segment.polymorph,
        basis,
        segment.lower_K,
        segment.upper_K,
        segment.boundary_labels,
    )


def _phase_change(label: str | None) -> tuple[str, str] | None:
    if label is None:
        return None
    match = _PHASE_CHANGE_RE.fullmatch(label)
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()


def _canonical_side(side: str) -> str:
    return " ".join(side.upper().split())


def _label_polymorph(side: str) -> Polymorph | None:
    canonical = _canonical_side(side)
    return _CRYSTAL_POLYMORPHS.get(canonical) or JANAF_LABEL_POLYMORPHS.get(canonical)


def _side_phase(side: str) -> Phase | None:
    if _label_polymorph(side) is not None:
        return Phase.CR
    return _PHASE_SIDE.get(_canonical_side(side))


def _side_polymorph(side: str, formula: str = "") -> Polymorph | None:
    token = _label_polymorph(side)
    if token is None:
        return None
    return canonicalize_janaf_transition(formula, token) if formula else token


def _transition_species_phase(
    label: str, formula: str
) -> tuple[State[Phase], State[Polymorph] | None]:
    """Phase of a labelled transition row.

    Both sides the same closed phase: that token. A crystal-crystal row does
    not pick one polymorph. Two different phases, or a side with no token,
    stay unknown — species.phase holds one phase.
    """

    parts = _phase_change(label)
    if parts is None:
        return State.unknown(f'transition spans phases named by "{label}"'), None
    left, right = parts
    left_phase, right_phase = _side_phase(left), _side_phase(right)
    unmapped = [
        side
        for side, phase in ((left, left_phase), (right, right_phase))
        if phase is None
    ]
    if unmapped:
        if len(unmapped) == 1:
            detail = (
                f"side {unmapped[0].strip()!r} is not a closed Phase or Polymorph token"
            )
        else:
            quoted = ", ".join(repr(side.strip()) for side in unmapped)
            detail = f"sides {quoted} are not closed Phase or Polymorph tokens"
        return (
            State.unknown(
                f'transition spans phases named by "{label}"; {detail}'
            ),
            None,
        )
    assert left_phase is not None and right_phase is not None
    if left_phase is not right_phase:
        return (
            State.unknown(
                f'transition spans phases named by "{label}" '
                f"({left_phase.value} -> {right_phase.value}); "
                "v2.1 species.phase has one phase axis and cannot hold both"
            ),
            None,
        )
    if left_phase is not Phase.CR:
        return State.of(left_phase), None
    left_poly = _side_polymorph(left, formula)
    right_poly = _side_polymorph(right, formula)
    if left_poly is not None and left_poly == right_poly:
        return State.of(left_phase), State.of(left_poly)
    left_name = left_poly.value if left_poly is not None else left.strip().lower()
    right_name = right_poly.value if right_poly is not None else right.strip().lower()
    return (
        State.of(left_phase),
        State.unknown(
            f"transition spans polymorphs {left_name} -> {right_name} "
            f'named by "{label}"; species.polymorph holds one token'
        ),
    )


def janaf_extract_phase(source_id: str, spelling: object) -> Phase | None:
    """JANAF-extract spelling of a printed phase, outside the global PHASE_MAP.

    ``ideal_gas`` is JANAF's IDEAL GAS / g. The same spelling is used by
    non-JANAF extracts; those stay unmapped. ``solid`` is not mapped: JANAF
    prints CRYSTAL and GLASS as different tokens.
    """

    if source_id != "janaf-4th":
        return None
    if str(spelling or "").strip().lower() == "ideal_gas":
        return Phase.G
    return None


def _phase_state(phase: Phase | None, reason: str) -> State[Phase]:
    return State.of(phase) if phase is not None else State.unknown(reason)


def _printed_heading_phase(name: str, title: str) -> Phase | None:
    heading = f"{name} {title}".upper()
    for spelling, phase in sorted(_PHASE_SIDE.items(), key=lambda item: -len(item[0])):
        if re.search(rf"(?<![A-Z]){re.escape(spelling)}(?![A-Z])", heading):
            return phase
    return None


def _polymorph_from_resolve(
    tag: StateTag, value: Polymorph | None, reason: str
) -> State[Polymorph]:
    if tag is StateTag.VALUE:
        if value is None:
            raise ValueError("resolved polymorph value is missing")
        return State.of(value)
    if tag is StateTag.NOT_APPLICABLE:
        return State.not_applicable(reason)
    return State.unknown(reason)


def _segments(
    state: str,
    formula: str,
    boundaries: Sequence[_Boundary],
    *,
    name: str = "",
    title: str = "",
) -> tuple[_Segment, ...]:
    if state in _SINGLE_PHASES and not (state == "cr" and boundaries):
        phase = _SINGLE_PHASES[state]
        tag, token, reason = resolve_janaf_polymorph(
            formula=formula,
            phase_is_crystal=phase is Phase.CR,
            name=name,
            title=title,
        )
        return (
            _Segment(
                0,
                State.of(phase),
                _polymorph_from_resolve(tag, token, reason),
                f"phase declared by JANAF index state {state!r}",
                None,
                None,
                (),
            ),
        )
    if state == "fl":
        reason = "JANAF state 'fl' is not a schema v2.1 Phase token"
        tag, token, poly_reason = resolve_janaf_polymorph(
            formula=formula,
            phase_is_crystal=False,
            name=name,
            title=title,
        )
        return (
            _Segment(
                0,
                State.unknown(reason),
                _polymorph_from_resolve(tag, token, poly_reason),
                reason,
                None,
                None,
                (),
            ),
        )
    if not boundaries:
        phase = _FIXED_REFERENCE_PHASES.get(formula) if state == "ref" else None
        convention = (
            "JANAF documented reference-phase convention for elemental reference "
            "tables (Ar, C, Cl, F, H, He, N, Ne, O)"
        )
        reason = (
            convention
            if phase is not None
            else f"{convention} does not name a phase for {formula}"
        )
        tag, token, poly_reason = resolve_janaf_polymorph(
            formula=formula,
            phase_is_crystal=phase is Phase.CR,
            name=name,
            title=title,
        )
        return (
            _Segment(
                0,
                _phase_state(phase, reason),
                _polymorph_from_resolve(tag, token, poly_reason),
                reason,
                None,
                None,
                (),
            ),
        )
    result: list[_Segment] = []
    for index in range(len(boundaries) + 1):
        lower = boundaries[index - 1].temperature if index else None
        upper = boundaries[index].temperature if index < len(boundaries) else None
        adjacent: list[tuple[str, str, Phase | None, str | None]] = []
        if index:
            prior = boundaries[index - 1]
            adjacent.append(
                (
                    prior.label,
                    prior.right,
                    _side_phase(prior.right),
                    _side_polymorph(prior.right, formula),
                )
            )
        if index < len(boundaries):
            following = boundaries[index]
            adjacent.append(
                (
                    following.label,
                    following.left,
                    _side_phase(following.left),
                    _side_polymorph(following.left, formula),
                )
            )
        phases = {phase for _label, _side, phase, _polymorph in adjacent if phase is not None}
        has_unknown = any(phase is None for _label, _side, phase, _polymorph in adjacent)
        if len(phases) > 1:
            detail = "; ".join(
                f'{side!r} in "{label}"' for label, side, _phase, _polymorph in adjacent
            )
            raise ValueError(f"conflicting JANAF segment phases: {detail}")
        if len(phases) == 1 and not has_unknown:
            phase_state = State.of(next(iter(phases)))
            phase_basis = "; ".join(
                f'{side!r} in "{label}"' for label, side, _phase, _polymorph in adjacent
            )
        else:
            quoted = "; ".join(
                f'"{label}"' for label, _side, _phase, _polymorph in adjacent
            )
            phase_basis = f"phase not named by adjacent JANAF transition label(s): {quoted}"
            phase_state = State.unknown(phase_basis)
        if phase_state.is_value and phase_state.value is Phase.CR:
            polymorphs = {
                polymorph
                for _label, _side, _phase, polymorph in adjacent
                if polymorph is not None
            }
            if len(polymorphs) > 1:
                polymorph_state = State.unknown(
                    "conflicting adjacent JANAF transition labels "
                    + ", ".join(sorted(p.value for p in polymorphs))
                    + f"; also consulted index_entry.name {name!r} and "
                    f"title_as_published {title!r}"
                )
            elif polymorphs:
                polymorph_state = State.of(next(iter(polymorphs)))
            else:
                tag, token, poly_reason = resolve_janaf_polymorph(
                    formula=formula,
                    phase_is_crystal=True,
                    name=name,
                    title=title,
                )
                if tag is StateTag.VALUE:
                    polymorph_state = _polymorph_from_resolve(tag, token, poly_reason)
                else:
                    quoted = "; ".join(
                        f'"{label}"' for label, _side, _phase, _polymorph in adjacent
                    )
                    polymorph_state = State.unknown(
                        "adjacent JANAF transition labels "
                        f"{quoted} do not name a crystal polymorph; {poly_reason}"
                    )
        elif phase_state.is_value:
            tag, token, poly_reason = resolve_janaf_polymorph(
                formula=formula,
                phase_is_crystal=False,
                name=name,
                title=title,
            )
            polymorph_state = _polymorph_from_resolve(tag, token, poly_reason)
        else:
            polymorph_state = State.unknown(
                "phase unknown; polymorph unresolved after consulting adjacent "
                f"JANAF transition labels, index_entry.name {name!r}, and "
                f"title_as_published {title!r}"
            )
        result.append(
            _Segment(
                index=index,
                phase=phase_state,
                polymorph=polymorph_state,
                phase_basis=phase_basis,
                lower_K=lower,
                upper_K=upper,
                boundary_labels=tuple(
                    label for label, _side, _phase, _polymorph in adjacent
                ),
            )
        )
    return tuple(result)


@lru_cache(maxsize=None)
def _element_reference_schedule(element: str) -> _ElementReferenceSchedule | None:
    """Load this element's printed (ref) table, if the corpus has one."""

    matches: list[tuple[Path, Mapping[str, Any], str]] = []
    for path in sorted(TABLES_DIR.glob(f"{element}-*.yaml")):
        table = _unwrap_table(load_table_document(path))
        entry = table.get("index_entry")
        if not isinstance(entry, Mapping) or entry.get("state") != "ref":
            continue
        formula = str(entry.get("formula_normalised") or "")
        composition = formula_composition(formula)
        if (
            composition is not None
            and len(composition) == 1
            and composition[0][0] == element
        ):
            matches.append((path, table, formula))
    if len(matches) != 1:
        return None

    path, table, formula = matches[0]
    table_id = str(table.get("table_id") or path.stem)
    entry = table.get("index_entry")
    assert isinstance(entry, Mapping)
    name = str(entry.get("name") or "")
    title = str(table.get("title_as_published") or "")
    heading_phase = _printed_heading_phase(name, title)
    structured = _structured_rows(table, table_id)
    printed_temperatures = table_printed_temperatures(
        row.temperature_token for row in structured
    )
    if not printed_temperatures:
        return None
    short, _refused = _short_rows(table, table_id)
    boundaries = tuple(
        _Boundary(row.temperature, row.label or "", *parts, row.order)
        for row in short
        if (parts := _phase_change(row.label)) is not None
    )
    if not boundaries:
        # The (ref) designation identifies the table, but this table prints no
        # phase schedule from which a Phase token can be taken.
        segments: tuple[_Segment, ...] = ()
    else:
        segments = _segments(
            "ref",
            formula,
            boundaries,
            name=name,
            title=title,
        )
    return _ElementReferenceSchedule(
        table_id=table_id,
        formula=formula,
        lower_K=min(printed_temperatures),
        upper_K=max(printed_temperatures),
        boundaries=boundaries,
        segments=segments,
        heading_phase=heading_phase,
    )


@lru_cache(maxsize=None)
def _element_reference_state(
    element: str, temperature: Decimal
) -> tuple[
    State[Species],
    Fraction | None,
    tuple[str, str, int | None, int | None],
    str,
    bool,
]:
    schedule = _element_reference_schedule(element)
    if schedule is None:
        reason = (
            f"JANAF printed corpus has no unique (ref) table for element {element}"
        )
        return (
            State.unknown(reason),
            None,
            (element, "missing", None, None),
            reason,
            False,
        )

    table_prefix = f"{schedule.table_id} (ref)"
    if not schedule.lower_K <= temperature <= schedule.upper_K:
        below = temperature < schedule.lower_K
        direction = "below" if below else "above"
        reason = (
            f"printed series temperature is {direction} the {table_prefix} range "
            f"{schedule.lower_K}–{schedule.upper_K} K"
        )
        return (
            State.unknown(reason),
            None,
            (element, schedule.table_id, -1 if below else -2, None),
            reason,
            False,
        )
    if not schedule.boundaries:
        if schedule.heading_phase is not None:
            species = make_species(
                schedule.formula, State.of(schedule.heading_phase), None
            )
            composition = formula_composition(schedule.formula)
            assert composition is not None and len(composition) == 1
            return (
                State.of(species),
                Fraction(str(composition[0][1])),
                (element, schedule.table_id, None, None),
                f"{table_prefix}, single phase printed in heading",
                False,
            )
        if schedule.formula in JANAF_REFERENCE_PHASE_BY_CONVENTION:
            # The table itself prints no phase; the allowed monograph
            # convention is explicitly carried as an observation notice.
            composition = formula_composition(schedule.formula)
            assert composition is not None and len(composition) == 1
            species = make_species(schedule.formula, State.of(Phase.G), None)
            return (
                State.of(species),
                Fraction(str(composition[0][1])),
                (element, schedule.table_id, None, None),
                f"{table_prefix}, ideal gas by monograph convention over printed T range",
                True,
            )
        reason = f"{table_prefix} prints no phase transition naming a phase"
        return (
            State.unknown(reason),
            None,
            (element, schedule.table_id, None, None),
            reason,
            False,
        )

    exact = tuple(
        boundary
        for boundary in schedule.boundaries
        if boundary.temperature == temperature
    )
    if exact:
        lines = tuple(boundary.row_order for boundary in exact)
        line_note = ", ".join(str(line) for line in lines)
        reason = (
            f"printed {table_prefix} transition row {line_note} is exact; "
            "both phases apply"
        )
        return (
            State.unknown(reason),
            None,
            (element, schedule.table_id, lines[0], lines[0]),
            reason,
            False,
        )

    lower = max(
        (boundary for boundary in schedule.boundaries if boundary.temperature < temperature),
        key=lambda boundary: boundary.temperature,
        default=None,
    )
    upper = min(
        (boundary for boundary in schedule.boundaries if boundary.temperature > temperature),
        key=lambda boundary: boundary.temperature,
        default=None,
    )
    lower_line = lower.row_order if lower is not None else None
    upper_line = upper.row_order if upper is not None else None
    if lower is not None and upper is not None:
        bracket = f"transition rows {lower_line} and {upper_line} bracket series T"
    elif lower is not None:
        bracket = f"transition row {lower_line} brackets lower series T"
    else:
        bracket = f"transition row {upper_line} brackets upper series T"
    locator_note = f"{table_prefix}, {bracket}"

    segment_index = sum(
        boundary.temperature < temperature for boundary in schedule.boundaries
    )
    segment = schedule.segments[segment_index]
    if segment.phase.is_unknown:
        reason = segment.phase.reason or f"{table_prefix} does not name a phase"
        return (
            State.unknown(reason),
            None,
            (element, schedule.table_id, lower_line, upper_line),
            f"{locator_note}; phase unknown: {reason}",
            False,
        )
    composition = formula_composition(schedule.formula)
    if composition is None or len(composition) != 1 or composition[0][0] != element:
        reason = f"{table_prefix} does not give a single-element reference formula"
        return (
            State.unknown(reason),
            None,
            (element, schedule.table_id, lower_line, upper_line),
            f"{locator_note}; {reason}",
            False,
        )
    reference_atom_count = Fraction(str(composition[0][1]))
    species = make_species(schedule.formula, segment.phase, segment.polymorph)
    return (
        State.of(species),
        reference_atom_count,
        (element, schedule.table_id, lower_line, upper_line),
        locator_note,
        False,
    )


def _formation_basis_at_temperature(
    formula: str, product: Species, temperature: Decimal
) -> _FormationBasis:
    composition = formula_composition(formula)
    if composition is None:
        reason = f"JANAF formula {formula!r} has no parsed formation composition"
        return _FormationBasis(
            State.unknown(reason),
            State.unknown(reason),
            (),
            reason,
        )

    terms = [ReactionTerm(product, Fraction(1))]
    formation_elements: list[tuple[str, Species]] = []
    reference_locator_key = []
    locator_notes = []
    convention_tables: list[tuple[str, str]] = []
    unknown_reasons = []
    for element, amount in composition:
        species_state, reference_atom_count, locator_key, locator_note, convention = (
            _element_reference_state(element, temperature)
        )
        reference_locator_key.append(locator_key)
        locator_notes.append(locator_note)
        if convention:
            convention_tables.append((element, locator_key[1]))
        if not species_state.is_value or reference_atom_count is None:
            unknown_reasons.append(species_state.reason or locator_note)
            continue
        reference_species = species_state.value
        formation_elements.append((element, reference_species))
        terms.append(
            ReactionTerm(
                reference_species,
                -Fraction(str(amount)) / reference_atom_count,
            )
        )

    locator_note = "printed JANAF (ref) schedules: " + "; ".join(locator_notes)
    key = tuple(reference_locator_key)
    if unknown_reasons:
        reason = "; ".join(unknown_reasons)
        return _FormationBasis(
            State.unknown(reason),
            State.unknown(reason),
            key,
            locator_note,
            tuple(convention_tables),
        )
    return _FormationBasis(
        State.of(Reaction(tuple(terms))),
        State.of(tuple(formation_elements)),
        key,
        locator_note,
        tuple(convention_tables),
    )


def _decimal_grain(token: str) -> Decimal:
    value = Decimal(token)
    return Decimal(1).scaleb(value.as_tuple().exponent)


def _source_disagreement_for(failure: Mapping[str, str]) -> Mapping[str, Any] | None:
    return next(
        (
            item
            for item in SOURCE_DISAGREEMENTS
            if item["table_id"] == failure.get("table_id")
            and item["temperature_as_published"] == failure.get("temperature_as_published")
            and item["identity"] == failure.get("identity")
        ),
        None,
    )


def _annotate_source_disagreements(
    failures: Sequence[Mapping[str, str]],
) -> list[dict[str, Any]]:
    annotated: list[dict[str, Any]] = []
    for failure in failures:
        match = _source_disagreement_for(failure)
        if match is None:
            annotated.append(dict(failure))
            continue
        annotated.append(
            {
                **failure,
                "kind": match["kind"],
                "reason": match["reason"],
                "quoted_evidence": match["quoted_evidence"],
            }
        )
    return annotated


def _printed_cell_erratum(
    table_id: str, row: _Row, column: str
) -> Mapping[str, Any] | None:
    return next(
        (
            erratum
            for erratum in STORED_CELL_ERRATA
            if erratum["table_id"] == table_id
            and erratum["temperature_as_published"] == row.temperature_token
            and erratum["line_number"] == row.line_number
            and erratum["column"] == column
        ),
        None,
    )


def _identity_failure(row: _Row, table_id: str) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    t = row.temperature
    if t <= 0:
        return failures
    s = row.cells.get("entropy")
    phi = row.cells.get("negative_gibbs_enthalpy_function")
    h = row.cells.get("enthalpy_increment")
    if s and phi and h and s.value is not None and phi.value is not None and h.value is not None:
        calculated = s.value - Decimal("1000") * h.value / t
        # Each printed decimal represents an interval of half its last-place grain.
        # Adding the interval radii gives a conservative rounding-only tolerance;
        # both H and T contributions use the smallest permitted denominator.
        e_t = _decimal_grain(row.temperature_token) / 2
        tolerance = (
            _decimal_grain(phi.token) / 2
            + _decimal_grain(s.token) / 2
            + Decimal("1000")
            * _decimal_grain(h.token)
            / (Decimal(2) * (t - e_t))
            + Decimal("1000") * abs(h.value) * e_t / (t * (t - e_t))
        )
        residual = abs(phi.value - calculated)
        if residual > tolerance:
            failures.append(
                {
                    "table_id": table_id,
                    "temperature_as_published": row.temperature_token,
                    "identity": "negative_gibbs_enthalpy_function",
                    "printed": str(phi.value),
                    "calculated": str(calculated),
                    "absolute_residual": str(residual),
                    "rounding_tolerance": str(tolerance),
                }
            )
    dg = row.cells.get("formation_gibbs_energy")
    logk = row.cells.get("log10_formation_equilibrium_constant")
    if row.numeric_tail is not None:
        dg = row.numeric_tail.get("formation_gibbs_energy")
        logk = row.numeric_tail.get("log10_formation_equilibrium_constant")
    if dg and logk and dg.value is not None and logk.value is not None:
        failure = _logk_identity_failure(
            table_id=table_id,
            temperature=t,
            temperature_token=row.temperature_token,
            delta_fG=dg.value,
            delta_fG_token=dg.token,
            log10_Kf=logk.value,
            log10_Kf_token=logk.token,
        )
        if failure is not None:
            failures.append(failure)
    return failures


def _logk_identity_failure(
    *,
    table_id: str,
    temperature: Decimal,
    temperature_token: str,
    delta_fG: Decimal,
    delta_fG_token: str,
    log10_Kf: Decimal,
    log10_Kf_token: str,
) -> dict[str, str] | None:
    if temperature <= 0:
        return None
    # Premise: Chase 1998 Monograph 9 p. 10 §3.4 prints
    # R = 8.314 41(26) J/(mol K), so this source-level transcription check
    # must use that printed constant rather than the helper's modern default.
    # Algebra: log10(Kf) = -1000*delta_fG/(R*T*ln(10)). Unit check:
    # (kJ/mol)*1000 J/kJ / ((J/(mol K))*K) is dimensionless. Worked row:
    # B-132 at 298.15 K with delta_fG=-5582.653 kJ/mol gives about
    # 978.04468, compatible with the printed 978.058 after input rounding.
    calculated = log10K_from_delta_fG_kJ_mol(
        delta_fG,
        temperature,
        gas_constant_J_per_mol_K=JANAF_R_J_PER_MOL_K,
    )
    e_t = _decimal_grain(temperature_token) / 2
    denominator = abs(
        log10K_from_delta_fG_kJ_mol(
            Decimal("1"),
            temperature - e_t,
            gas_constant_J_per_mol_K=JANAF_R_J_PER_MOL_K,
        )
    )
    temperature_term = abs(calculated) * e_t / (temperature - e_t)
    tolerance = (
        _decimal_grain(log10_Kf_token) / 2
        + _decimal_grain(delta_fG_token) / 2 * denominator
        + temperature_term
    )
    residual = abs(log10_Kf - calculated)
    if residual <= tolerance:
        return None
    return {
        "table_id": table_id,
        "temperature_as_published": temperature_token,
        "identity": "log10_Kf_from_delta_fG",
        "printed": str(log10_Kf),
        "calculated": str(calculated),
        "absolute_residual": str(residual),
        "rounding_tolerance": str(tolerance),
    }


def _scale_error_identity_failure(row: _Row, table_id: str) -> dict[str, str] | None:
    """Refuse a stored pair whose identity residual exceeds 10× rounding tolerance."""

    if row.numeric_tail is not None:
        return None
    delta_g = row.cells.get("formation_gibbs_energy")
    log_k = row.cells.get("log10_formation_equilibrium_constant")
    if (
        delta_g is None
        or log_k is None
        or delta_g.value is None
        or log_k.value is None
    ):
        return None
    failure = _logk_identity_failure(
        table_id=table_id,
        temperature=row.temperature,
        temperature_token=row.temperature_token,
        delta_fG=delta_g.value,
        delta_fG_token=delta_g.token,
        log10_Kf=log_k.value,
        log10_Kf_token=log_k.token,
    )
    if failure is None:
        return None
    residual = Decimal(failure["absolute_residual"])
    tolerance = Decimal(failure["rounding_tolerance"])
    if residual > SCALE_ERROR_FACTOR * tolerance:
        return failure
    return None


def _neighbour_sign_hits(observations: Sequence[Observation]) -> list[dict[str, str]]:
    """Advisory scan: a stored value whose sign opposes both nearest neighbours."""

    hits: list[dict[str, str]] = []
    for observation in observations:
        series = list(observation.value.series or ())
        if len(series) < 3:
            continue
        quantity = observation.identity.quantity.value.value
        for index, (temperature, value) in enumerate(series):
            if value == 0:
                continue
            left = None
            right = None
            for previous in reversed(series[:index]):
                if previous[1] != 0:
                    left = previous[1]
                    break
            for following in series[index + 1 :]:
                if following[1] != 0:
                    right = following[1]
                    break
            if left is None or right is None:
                continue
            opposite_both = (value > 0 > left and right < 0) or (
                value < 0 < left and right > 0
            )
            if not opposite_both:
                continue
            hits.append(
                {
                    "quantity": quantity,
                    "temperature_as_published": str(temperature),
                    "value": str(value),
                    "left_neighbour": str(left),
                    "right_neighbour": str(right),
                    "status": NEIGHBOUR_SIGN_SCAN_STATUS,
                }
            )
    return hits


def _observation_series_key(observation: Observation) -> str:
    """Series group key shared by quantities in one compound/ref-state window."""

    parts = observation.observation_id.split(":", 3)
    return (
        f"{parts[0]}:{parts[1]}:{parts[3]}"
        if len(parts) == 4
        else observation.observation_id
    )


def _stored_pair_identity_denominator(
    observations: Sequence[Observation],
    failures: Sequence[Mapping[str, str]],
) -> dict[str, int]:
    """Eligible / checked / passed for stored-pair identity.

    Eligible is every emitted T>0 point of delta_fG or log10_Kf. Checked is
    the T>0 intersection of those series in the same segment. A high pass
    rate cannot hide a small denominator: checked can be smaller than
    either eligible count when one member is missing.
    """

    by_segment: dict[str, dict[Quantity, Observation]] = defaultdict(dict)
    for observation in observations:
        quantity = observation.identity.quantity.value
        if quantity in {Quantity.DELTA_FG, Quantity.LOG10_KF}:
            by_segment[_observation_series_key(observation)][quantity] = observation
    eligible_delta_fG = 0
    eligible_log10_Kf = 0
    checked = 0
    for quantity_map in by_segment.values():
        delta_g = quantity_map.get(Quantity.DELTA_FG)
        log_k = quantity_map.get(Quantity.LOG10_KF)
        delta_g_temperatures = (
            {point[0] for point in (delta_g.value.series or ()) if point[0] > 0}
            if delta_g is not None
            else set()
        )
        log_k_temperatures = (
            {point[0] for point in (log_k.value.series or ()) if point[0] > 0}
            if log_k is not None
            else set()
        )
        eligible_delta_fG += len(delta_g_temperatures)
        eligible_log10_Kf += len(log_k_temperatures)
        checked += len(delta_g_temperatures & log_k_temperatures)
    failed = len(failures)
    return {
        "eligible_delta_fG_T_gt_0": eligible_delta_fG,
        "eligible_log10_Kf_T_gt_0": eligible_log10_Kf,
        "checked_intersection": checked,
        "passed": checked - failed,
        "failed": failed,
    }


def _stored_pair_identity_failures_from_observations(
    observations: Sequence[Observation],
    table_id: str,
) -> list[dict[str, str]]:
    """Judge log Kf against delta_fG from the emitted series, not source tokens."""

    by_segment: dict[str, dict[Quantity, Observation]] = defaultdict(dict)
    for observation in observations:
        quantity = observation.identity.quantity.value
        if quantity not in {Quantity.DELTA_FG, Quantity.LOG10_KF}:
            continue
        segment = _observation_series_key(observation)
        by_segment[segment][quantity] = observation
    failures: list[dict[str, str]] = []
    for quantity_map in by_segment.values():
        delta_g = quantity_map.get(Quantity.DELTA_FG)
        log_k = quantity_map.get(Quantity.LOG10_KF)
        if delta_g is None or log_k is None:
            continue
        delta_g_points = {
            temperature: value for temperature, value in (delta_g.value.series or ())
        }
        log_k_points = {
            temperature: value for temperature, value in (log_k.value.series or ())
        }
        for temperature, delta_fG in delta_g_points.items():
            log10_Kf = log_k_points.get(temperature)
            if log10_Kf is None:
                continue
            failure = _logk_identity_failure(
                table_id=table_id,
                temperature=temperature,
                temperature_token=str(temperature),
                delta_fG=delta_fG,
                delta_fG_token=str(delta_fG),
                log10_Kf=log10_Kf,
                log10_Kf_token=str(log10_Kf),
            )
            if failure is not None:
                failures.append(failure)
    return failures


def _corroborate_structured_rows(
    rows: list[_Row], _table_id: str
) -> tuple[list[_Row], list[dict[str, Any]]]:
    """Refuse structured rows whose T is not in this table's printed T set."""

    ordered = sorted(rows, key=lambda row: row.order)
    printed = table_printed_temperatures(row.temperature_token for row in ordered)
    kept: list[_Row] = []
    refused: list[dict[str, Any]] = []
    for row in ordered:
        if row.temperature not in printed:
            refused.append(
                {
                    "line_number": row.line_number,
                    "raw_text": row.raw_line or row.temperature_token,
                    "reason": GRID_RANGE_REASON,
                    "temperature_as_published": row.temperature_token,
                }
            )
            continue
        kept.append(row)
    changed = True
    while changed:
        changed = False
        for index in range(len(kept) - 1):
            if kept[index].temperature <= kept[index + 1].temperature:
                continue
            previous_t = kept[index - 1].temperature if index else None
            drop_index = (
                index
                if previous_t is None or previous_t <= kept[index + 1].temperature
                else index + 1
            )
            dropped = kept.pop(drop_index)
            refused.append(
                {
                    "line_number": dropped.line_number,
                    "raw_text": dropped.raw_line or dropped.temperature_token,
                    "reason": GRID_ORDER_REASON,
                    "temperature_as_published": dropped.temperature_token,
                }
            )
            changed = True
            break
    kept.sort(key=lambda row: row.order)
    return kept, refused


def _leftover_ambiguity_rows(
    table: Mapping[str, Any],
    *,
    used_line_numbers: set[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    markers: list[dict[str, Any]] = []
    refused_layout: list[dict[str, Any]] = []
    for item in table.get("parse_ambiguities") or ():
        if not isinstance(item, Mapping) or not item.get("raw_line"):
            continue
        line_number = item.get("line_number")
        if isinstance(line_number, int) and line_number in used_line_numbers:
            continue
        raw_text = str(item["raw_line"])
        record = {
            "line_number": line_number,
            "raw_text": raw_text,
            "reason": str(item.get("reason") or ""),
        }
        kind = item.get("kind")
        if kind == NON_DATA_MARKER_KIND or (
            _raw_numeric_token_count(raw_text) == 0
            and not _NUMBER_RE.fullmatch(_trimmed_tab_fields(raw_text)[0] if _trimmed_tab_fields(raw_text) else "")
        ):
            record["reason"] = str(item.get("reason") or NON_DATA_MARKER_REASON)
            markers.append(record)
        elif kind == REFUSED_LAYOUT_KIND or _raw_numeric_token_count(raw_text) > 0:
            refused_layout.append(record)
        else:
            markers.append(record)
    return markers, refused_layout


def _subtype(left: str, right: str) -> str:
    def token(side: str) -> str:
        canonical = "LIQUID" if side.strip().upper() == "LIQ" else side.strip().upper()
        return "-".join(canonical.lower().split())

    return f"{token(left)}-{token(right)}"


def _observation(
    *,
    table_id: str,
    formula: str,
    phase: State[Phase],
    polymorph: State[Polymorph] | None,
    charge: State[int] | int | None = None,
    quantity: Quantity,
    value: Value,
    source_path: str,
    download_url: str,
    segment: _Segment | None,
    null_count: int = 0,
    subtype: str | None = None,
    transition_label: str | None = None,
    transition_pressure_Pa: Decimal | None = None,
    transition_pressure_basis: str | None = None,
    formation_basis: _FormationBasis | None = None,
) -> Observation:
    species = make_species(formula, phase, polymorph, charge=charge)
    known: dict[str, Any] = {}
    if quantity is Quantity.TRANSITION_TEMPERATURE:
        known["subtype"] = State.of(subtype or "phase-transition")
        known["total_pressure_Pa"] = State.of(
            transition_pressure_Pa
            if transition_pressure_Pa is not None
            else STANDARD_PRESSURE_PA
        )
    else:
        known["per"] = State.of(PerBasis.MOL_SPECIES)
        known["temperature_K"] = State.unknown(
            "temperature varies along the series and is stored as the T_K coordinate"
        )
        known["standard_pressure_Pa"] = State.of(STANDARD_PRESSURE_PA)
        if quantity is Quantity.H_MINUS_H298:
            known["subtype"] = State.of("H(T)-H(298.15 K)")
        if quantity in {Quantity.DELTA_FH, Quantity.DELTA_FG, Quantity.LOG10_KF}:
            if formation_basis is None:
                reason = (
                    "JANAF formation observation has no printed T coordinate "
                    "for the element reference-state schedule"
                )
                known["reaction"] = State.unknown(reason)
                known["formation_elements"] = State.unknown(reason)
            else:
                known["reaction"] = formation_basis.reaction
                known["formation_elements"] = formation_basis.formation_elements
    identity = fill_identity(quantity, species, **known)
    notices = tuple(
        Notice(
            kind=NoticeKind.REFERENCE_PHASE_BY_CONVENTION,
            affected_quantities=(quantity,),
            reason=(
                "JANAF web table prints no phase; ideal-gas reference state per "
                "the monograph convention, page not held"
            ),
            origin=f"{reference_table} ({reference_element}) reference-element table",
            authority=Authority.CONVENTION,
            certification="fetch the JANAF 4th ed. printed page for the table",
        )
        for reference_element, reference_table in (
            () if formation_basis is None else formation_basis.convention_tables
        )
        if quantity in {Quantity.DELTA_FH, Quantity.DELTA_FG, Quantity.LOG10_KF}
    )
    role_note = (
        "compilation_role engine_reference_input=true, scoring_eligible=false; "
        f"circularity_warning={CIRCULARITY_WARNING}"
    )
    if transition_label is not None:
        relation = (
            f'Direct JANAF labelled row "{transition_label}"; '
            f"total_pressure_basis={transition_pressure_basis}; {role_note}"
        )
    else:
        relation = (
            f"Direct JANAF tabulation; {null_count} printed cells blank or INFINITE; "
            f"{role_note}"
        )
        if quantity in {Quantity.DELTA_FH, Quantity.DELTA_FG, Quantity.LOG10_KF}:
            if formation_basis is not None:
                relation += (
                    "; formation_reference_state_schedule="
                    f"{formation_basis.locator_note}"
                )
        if segment is not None:
            relation += f"; phase_basis={segment.phase_basis}"
    locator_note = f"NIST download_url: {download_url}"
    identity_suffix = ""
    if quantity in {Quantity.DELTA_FH, Quantity.DELTA_FG, Quantity.LOG10_KF}:
        if formation_basis is not None:
            locator_note += f"; {formation_basis.locator_note}"
            identity_suffix = f":{formation_basis.identity_suffix}"
    locator = Locator(
        table=table_id,
        source_path=source_path,
        record=table_id,
        note=locator_note,
    )
    if quantity is Quantity.TRANSITION_TEMPERATURE:
        # Content-stable: printed T, not the mutable subtype label (F4 R-label).
        t_tok = (
            format_temperature_token(value.point)
            if value.point is not None
            else (subtype or "phase-transition")
        )
        suffix = f"transition_temperature:T={t_tok}"
    else:
        assert segment is not None
        suffix = phase_window_suffix(
            quantity.value,
            lower_K=segment.lower_K,
            upper_K=segment.upper_K,
        )
    return Observation(
        observation_id=f"{SOURCE_ID}:{table_id}:{suffix}{identity_suffix}",
        experiment_id=f"{SOURCE_ID}:{table_id}:tabulation",
        identity=identity,
        value=value,
        uncertainty=Uncertainty(kind=UncertaintyKind.NONE),
        evidence=Evidence(
            class_=State.of(EvidenceClass.COMPILATION_ASSESSED),
            original_method_class="assessed_thermodynamic_functions",
            model="assessed_thermodynamic_functions",
        ),
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
            parameters=(),
            output_unit=QUANTITY_UNITS[quantity],
            pure_substance_reference=True
            if COMPILATION_ROLE.get("pure_substance_reference") is True
            else None,
        ),
    )


def generate_table(
    payload: Mapping[str, Any],
    *,
    source_path: str | None = None,
) -> TableGeneration:
    """Purely transform one parsed JANAF table payload into observations and audit data."""

    table = _unwrap_table(payload)
    table_id, formula, charge, state = _validate_table(table)
    index_entry = table.get("index_entry") if isinstance(table.get("index_entry"), Mapping) else {}
    printed_name = str(index_entry.get("name") or "")
    printed_title = str(table.get("title_as_published") or "")
    source_path = source_path or f"{TABLE_SOURCE_PREFIX}/{table_id}.yaml"
    download_url = str(table.get("download_url") or "")
    if not download_url:
        raise ValueError(f"{table_id}: missing NIST download_url")
    structured = _structured_rows(table, table_id)
    structured, refused_grid_rows = _corroborate_structured_rows(structured, table_id)
    short, refused_concatenated_rows = _short_rows(table, table_id)
    used_line_numbers = {
        row.line_number
        for row in short
        if isinstance(row.line_number, int)
    }
    used_line_numbers.update(
        int(row["line_number"])
        for row in refused_concatenated_rows
        if isinstance(row.get("line_number"), int)
    )
    non_data_marker_lines, leftover_layout_rows = _leftover_ambiguity_rows(
        table, used_line_numbers=used_line_numbers
    )
    refused_layout_rows = leftover_layout_rows + refused_grid_rows
    all_rows = structured + short
    boundary_rows: list[_Boundary] = []
    for row in short:
        parts = _phase_change(row.label)
        is_named_crystal_transition = (
            parts is not None
            and _canonical_side(parts[0]) in _CRYSTAL_POLYMORPHS
            and _canonical_side(parts[1]) in _CRYSTAL_POLYMORPHS
        )
        if parts is not None and (
            state in _COMBINED_STATES
            or (state == "cr" and is_named_crystal_transition)
        ):
            boundary_rows.append(
                _Boundary(row.temperature, row.label or "", parts[0], parts[1], row.order)
            )
    boundary_rows.sort(key=lambda item: item.row_order)
    segments = _segments(
        state,
        formula,
        boundary_rows,
        name=printed_name,
        title=printed_title,
    )

    def segment_index(row: _Row) -> int:
        if len(segments) == 1:
            return 0
        # Physical row order, not a right-bisect, owns a same-T row's printed
        # side. JANAF's regular full grid row is printed before an equal-T
        # labelled boundary; short rows retain their harvested source lines, so
        # only short rows following that boundary enter the upper segment.
        return sum(
            boundary.temperature < row.temperature
            or (
                boundary.temperature == row.temperature
                and row.is_short
                and boundary.row_order < row.order
            )
            for boundary in boundary_rows
        )

    accounting: dict[str, dict[str, Any]] = {
        column: {
            "structured_numeric": 0,
            "short_row_numeric": 0,
            "numeric_source_cells": 0,
            "stored_points": 0,
            "printed_null_cells": 0,
            "excluded_numeric": Counter(),
        }
        for column in _UNITS
        if column != "temperature"
    }
    nulls: dict[tuple[int, str], int] = Counter()
    applied_cell_errata: list[dict[str, Any]] = []
    scale_error_by_order = {
        row.order: failure
        for row in structured
        if (failure := _scale_error_identity_failure(row, table_id)) is not None
    }
    scale_error_refusals: list[dict[str, Any]] = []
    for row in structured:
        seg = segment_index(row)
        for column, cell in row.cells.items():
            if column == "temperature":
                continue
            if cell.value is None:
                accounting[column]["printed_null_cells"] += 1
                nulls[(seg, column)] += 1
            else:
                accounting[column]["structured_numeric"] += 1
                accounting[column]["numeric_source_cells"] += 1
                if column in _FORMATION_PAIR_COLUMNS and row.order in scale_error_by_order:
                    accounting[column]["excluded_numeric"][
                        SCALE_ERROR_REFUSAL_REASON
                    ] += 1
        if row.order in scale_error_by_order:
            scale_error_refusals.append(
                {
                    **scale_error_by_order[row.order],
                    "reason": SCALE_ERROR_REFUSAL_REASON,
                    "line_number": row.line_number,
                }
            )
    for row in short:
        seg = segment_index(row)
        for column, cell in row.cells.items():
            if column == "temperature":
                continue
            if cell.value is None:
                accounting[column]["printed_null_cells"] += 1
                nulls[(seg, column)] += 1
            else:
                accounting[column]["short_row_numeric"] += 1
                accounting[column]["numeric_source_cells"] += 1
                erratum = _printed_cell_erratum(table_id, row, column)
                if erratum is not None:
                    accounting[column]["excluded_numeric"][
                        PRINTED_CELL_ERRATUM_REASON
                    ] += 1
                    applied_cell_errata.append(
                        {
                            **erratum,
                            "disposition": "refused",
                            "reason": PRINTED_CELL_ERRATUM_REASON,
                        }
                    )
        if row.numeric_tail is not None:
            for column, cell in row.numeric_tail.items():
                if cell.value is None:
                    accounting[column]["printed_null_cells"] += 1
                    nulls[(seg, column)] += 1
                    continue
                accounting[column]["short_row_numeric"] += 1
                accounting[column]["numeric_source_cells"] += 1
                if cell.value != 0:
                    accounting[column]["excluded_numeric"][
                        MERGED_FORMATION_REFUSAL_REASON
                    ] += 1

    ambiguity_raw_lines = [
        str(item["raw_line"])
        for item in table.get("parse_ambiguities") or ()
        if isinstance(item, Mapping) and item.get("raw_line")
    ]
    raw_numeric_source_tokens = sum(
        int(row["structured_numeric"]) for row in accounting.values()
    ) + sum(_raw_numeric_token_count(line) for line in ambiguity_raw_lines)
    accounted_numeric_cells = sum(
        int(row["numeric_source_cells"]) for row in accounting.values()
    )
    refused_concatenated_numeric_tokens = sum(
        _raw_numeric_token_count(str(row["raw_text"]))
        for row in refused_concatenated_rows
    )
    refused_layout_numeric_tokens = sum(
        _raw_numeric_token_count(str(row["raw_text"]))
        for row in refused_layout_rows
    )
    unexplained_raw_numeric_tokens = (
        raw_numeric_source_tokens
        - accounted_numeric_cells
        - refused_concatenated_numeric_tokens
        - refused_layout_numeric_tokens
    )
    if unexplained_raw_numeric_tokens:
        raise AssertionError(
            f"{table_id}: unexplained raw numeric tokens: "
            f"source={raw_numeric_source_tokens} accounted={accounted_numeric_cells} "
            f"refused={refused_concatenated_numeric_tokens}"
        )

    glass_markers = _glass_liquid_markers(short) if state == "l" else ()
    if len(glass_markers) == 1 and len(segments) == 1:
        marker = glass_markers[0]
        temperature_token = next(
            row.temperature_token for row in short if row.order == marker.row_order
        )
        segments = (
            _glass_region_segment(
                segments[0],
                marker,
                all_rows,
                table_id=table_id,
                scale_error_orders=set(scale_error_by_order),
                temperature_token=temperature_token,
            ),
        )

    points: dict[tuple[int, str], list[tuple[Decimal, Decimal, int]]] = defaultdict(list)
    for row in all_rows:
        seg = segment_index(row)
        for column in _COLUMN_QUANTITIES:
            cell = (
                row.numeric_tail.get(column)
                if row.numeric_tail is not None and column in _FORMATION_TAIL_COLUMNS
                else row.cells.get(column)
            )
            if cell is not None and cell.value is not None:
                if _printed_cell_erratum(table_id, row, column) is not None:
                    continue
                if (
                    row.numeric_tail is not None
                    and column in _FORMATION_TAIL_COLUMNS
                    and cell.value != 0
                ):
                    continue
                if (
                    column in _FORMATION_PAIR_COLUMNS
                    and row.order in scale_error_by_order
                ):
                    continue
                points[(seg, column)].append((row.temperature, cell.value, row.order))
                accounting[column]["stored_points"] += 1
        phi = row.cells.get("negative_gibbs_enthalpy_function")
        if phi is not None and phi.value is not None:
            accounting["negative_gibbs_enthalpy_function"]["excluded_numeric"][
                "derived from S and H-H(Tr); retained as a transcription check"
            ] += 1

    observations: list[Observation] = []
    counts_by_segment: dict[str, Counter[str]] = defaultdict(Counter)
    formation_basis_cache: dict[tuple[int, Decimal], _FormationBasis] = {}
    for segment in segments:
        for column, quantity in _COLUMN_QUANTITIES.items():
            series = sorted(points.get((segment.index, column), ()), key=lambda p: (p[0], p[2]))
            if series and quantity in {
                Quantity.DELTA_FH,
                Quantity.DELTA_FG,
                Quantity.LOG10_KF,
            }:
                product = make_species(
                    formula, segment.phase, segment.polymorph, charge=charge
                )
                grouped: dict[
                    tuple[State[Reaction], State[tuple[tuple[str, Species], ...]], tuple],
                    tuple[_FormationBasis, list[tuple[Decimal, Decimal, int]]],
                ] = {}
                for point in series:
                    basis_key = (segment.index, point[0])
                    basis = formation_basis_cache.get(basis_key)
                    if basis is None:
                        basis = _formation_basis_at_temperature(
                            formula, product, point[0]
                        )
                        formation_basis_cache[basis_key] = basis
                    key = (
                        basis.reaction,
                        basis.formation_elements,
                        basis.reference_locator_key,
                    )
                    if key not in grouped:
                        grouped[key] = (basis, [])
                    grouped[key][1].append(point)
                for basis, basis_series in grouped.values():
                    value = Value(
                        kind=ValueKind.SERIES,
                        series=tuple(
                            (temperature, amount)
                            for temperature, amount, _ in basis_series
                        ),
                    )
                    observations.append(
                        _observation(
                            table_id=table_id,
                            formula=formula,
                            phase=segment.phase,
                            polymorph=segment.polymorph,
                            charge=charge,
                            quantity=quantity,
                            value=value,
                            source_path=source_path,
                            download_url=download_url,
                            segment=segment,
                            null_count=nulls[(segment.index, column)],
                            formation_basis=basis,
                        )
                    )
                    counts_by_segment[f"segment-{segment.index}"][quantity.value] += 1
                continue

            value = (
                Value(
                    kind=ValueKind.SERIES,
                    series=tuple((temperature, amount) for temperature, amount, _ in series),
                )
                if series
                else Value(
                    kind=ValueKind.UNAVAILABLE,
                    unavailable_reason=(
                        f"phase segment has no numeric {column} cell; "
                        f"{nulls[(segment.index, column)]} printed cells blank or INFINITE"
                    ),
                )
            )
            observations.append(
                _observation(
                    table_id=table_id,
                    formula=formula,
                    phase=segment.phase,
                    polymorph=segment.polymorph,
                    charge=charge,
                    quantity=quantity,
                    value=value,
                    source_path=source_path,
                    download_url=download_url,
                    segment=segment,
                    null_count=nulls[(segment.index, column)],
                )
            )
            counts_by_segment[f"segment-{segment.index}"][quantity.value] += 1

    transition_rows: list[dict[str, Any]] = []
    non_transition_rows: list[dict[str, Any]] = []
    vocabulary_gaps: list[dict[str, Any]] = []
    ordered_short = sorted(short, key=lambda row: row.order)
    adjacent_condition_rows: list[dict[str, Any]] = []
    transition_pressures: dict[int, tuple[Decimal, str]] = {}
    for row_index, row in enumerate(ordered_short):
        condition = _CONDITION_RE.fullmatch(row.label or "")
        if condition is None:
            continue
        neighbors = (
            ordered_short[row_index - 1] if row_index else None,
            ordered_short[row_index + 1]
            if row_index + 1 < len(ordered_short)
            else None,
        )
        transition = next(
            (
                candidate
                for candidate in neighbors
                if candidate is not None
                and candidate.temperature == row.temperature
                and _phase_change(candidate.label) is not None
            ),
            None,
        )
        if transition is None:
            continue
        adjacent_condition_rows.append(
            {
                "temperature_as_published": row.temperature_token,
                "label": row.label,
                "line_number": row.line_number,
                "transition_label": transition.label,
                "transition_line_number": transition.line_number,
                "assigned_segment": f"segment-{segment_index(row)}",
            }
        )
        if condition.group(1).upper() == "PRESSURE":
            transition_pressures[transition.order] = (
                Decimal(condition.group(2)) * STANDARD_PRESSURE_PA,
                f'adjacent JANAF row "{row.label}"',
            )
    for row_index, row in enumerate(ordered_short):
        if row.label is None:
            continue
        parts = _phase_change(row.label)
        if parts is None:
            non_transition_rows.append(
                {
                    "temperature_as_published": row.temperature_token,
                    "label": row.label,
                    "line_number": row.line_number,
                    "raw_text": row.tail_text,
                    "reason": "label does not name a phase change on both sides",
                }
            )
            continue
        subtype = _subtype(*parts)
        pressure, pressure_basis = transition_pressures.get(
            row.order,
            (
                STANDARD_PRESSURE_PA,
                f'table declared standard state "{STANDARD_STATE}"; no adjacent PRESSURE row',
            ),
        )
        transition_rows.append(
            {
                "temperature_as_published": row.temperature_token,
                "label": row.label,
                "subtype": subtype,
                "line_number": row.line_number,
                "total_pressure_Pa": str(pressure),
                "total_pressure_basis": pressure_basis,
            }
        )
        transition_phase, transition_polymorph = _transition_species_phase(
            row.label or "", formula
        )
        observations.append(
            _observation(
                table_id=table_id,
                formula=formula,
                phase=transition_phase,
                polymorph=transition_polymorph,
                charge=charge,
                quantity=Quantity.TRANSITION_TEMPERATURE,
                value=Value.point_of(row.temperature),
                source_path=source_path,
                download_url=download_url,
                segment=None,
                subtype=subtype,
                transition_label=row.label,
                transition_pressure_Pa=pressure,
                transition_pressure_basis=pressure_basis,
            )
        )
        next_row = ordered_short[row_index + 1] if row_index + 1 < len(ordered_short) else None
        left_h = row.cells.get("enthalpy_increment")
        right_h = next_row.cells.get("enthalpy_increment") if next_row else None
        gap: dict[str, Any] = {
            "temperature_as_published": row.temperature_token,
            "label": row.label,
            "gap": "enthalpy_of_transition quantity is absent from schema v2.1",
        }
        if (
            next_row is not None
            and next_row.temperature == row.temperature
            and left_h is not None
            and right_h is not None
            and left_h.value is not None
            and right_h.value is not None
        ):
            gap["printed_jump_H_minus_H298_kJ_mol"] = str(right_h.value - left_h.value)
        else:
            gap["printed_jump_H_minus_H298_kJ_mol"] = None
            gap["reason"] = "paired phase row is not present in this table"
        vocabulary_gaps.append(gap)

    failures: list[dict[str, str]] = []
    stored_pair_failures: list[dict[str, str]] = []
    refused_merged_pair_failures: list[dict[str, Any]] = []
    checks = Counter()
    refused_merged_pair_checks = 0
    for row in all_rows:
        t = row.temperature
        if t > 0:
            if all(
                row.cells.get(column) is not None
                and row.cells[column].value is not None
                for column in (
                    "entropy",
                    "negative_gibbs_enthalpy_function",
                    "enthalpy_increment",
                )
            ):
                checks["negative_gibbs_enthalpy_function"] += 1
            dg = (
                row.numeric_tail.get("formation_gibbs_energy")
                if row.numeric_tail
                else row.cells.get("formation_gibbs_energy")
            )
            logk = (
                row.numeric_tail.get("log10_formation_equilibrium_constant")
                if row.numeric_tail
                else row.cells.get("log10_formation_equilibrium_constant")
            )
            if dg is not None and logk is not None and dg.value is not None and logk.value is not None:
                pair_is_stored = row.numeric_tail is None or (
                    dg.value == 0 and logk.value == 0
                )
                if not pair_is_stored:
                    refused_merged_pair_checks += 1
                    for failure in _identity_failure(row, table_id):
                        if failure["identity"] == "log10_Kf_from_delta_fG":
                            refused_merged_pair_failures.append(
                                {
                                    **failure,
                                    "line_number": row.line_number,
                                    "raw_text": row.tail_text,
                                    "assigned_segment": f"segment-{segment_index(row)}",
                                }
                            )
            for failure in _identity_failure(row, table_id):
                if failure["identity"] == "negative_gibbs_enthalpy_function":
                    failures.append(failure)
    neighbour_sign_hits = _neighbour_sign_hits(observations)
    stored_pair_failures = _annotate_source_disagreements(
        _stored_pair_identity_failures_from_observations(observations, table_id)
    )
    stored_pair_denominator = _stored_pair_identity_denominator(
        observations, stored_pair_failures
    )
    checks["log10_Kf_from_delta_fG"] = stored_pair_denominator["checked_intersection"]
    failures.extend(stored_pair_failures)

    plain_accounting: dict[str, Any] = {}
    for column, row in accounting.items():
        excluded = dict(sorted(row["excluded_numeric"].items()))
        excluded_total = sum(excluded.values())
        if row["numeric_source_cells"] != row["stored_points"] + excluded_total:
            raise AssertionError(
                f"{table_id}: unexplained {column} cells: source={row['numeric_source_cells']} "
                f"stored={row['stored_points']} excluded={excluded_total}"
            )
        plain_accounting[column] = {
            **{key: value for key, value in row.items() if key != "excluded_numeric"},
            "excluded_numeric": excluded,
            "excluded_numeric_total": excluded_total,
            "unexplained_numeric": 0,
        }
    report = {
        "table_id": table_id,
        "formula": formula,
        "state_as_published": state,
        "transition_row_assignment": (
            "For combined and reference tables, and named-polymorph boundaries in "
            "crystal tables, physical file order assigns every same-temperature row: "
            "rows through the labelled transition stay on its printed left side and "
            "following rows use its right side."
        ),
        "phase_segments": [
            {
                "segment": f"segment-{segment.index}",
                "phase": to_plain(segment.phase),
                "polymorph": to_plain(segment.polymorph),
                "phase_basis": segment.phase_basis,
                "lower_boundary_K": None if segment.lower_K is None else str(segment.lower_K),
                "upper_boundary_K": None if segment.upper_K is None else str(segment.upper_K),
                "boundary_labels": list(segment.boundary_labels),
                "observation_counts": dict(sorted(counts_by_segment[f"segment-{segment.index}"].items())),
            }
            for segment in segments
        ],
        "cell_accounting": plain_accounting,
        "raw_numeric_accounting": {
            "numeric_source_tokens": raw_numeric_source_tokens,
            "accounted_numeric_cells": accounted_numeric_cells,
            "refused_concatenated_numeric_tokens": (
                refused_concatenated_numeric_tokens
            ),
            "refused_layout_numeric_tokens": refused_layout_numeric_tokens,
            "unexplained_numeric_tokens": 0,
        },
        "transcription_checks": dict(checks),
        "transcription_gas_constant_J_per_mol_K": str(JANAF_R_J_PER_MOL_K),
        "transcription_identity_failures": failures,
        "stored_pair_identity_failures": stored_pair_failures,
        "stored_pair_identity_denominator": stored_pair_denominator,
        "source_disagreements": [
            item for item in SOURCE_DISAGREEMENTS if item["table_id"] == table_id
        ],
        "scale_error_refusals": scale_error_refusals,
        "neighbour_sign_hits": neighbour_sign_hits,
        "neighbour_sign_scan": {
            "status": NEIGHBOUR_SIGN_SCAN_STATUS,
            "caught_by": NEIGHBOUR_SIGN_SCAN_CATCHES,
        },
        "gibbs_identity_scan": {
            "status": GIBBS_IDENTITY_SCAN_STATUS,
        },
        "refused_merged_pair_checks": refused_merged_pair_checks,
        "refused_merged_pair_identity_failures": refused_merged_pair_failures,
        "transition_rows": transition_rows,
        "non_transition_rows": non_transition_rows,
        "adjacent_condition_rows": adjacent_condition_rows,
        "vocabulary_gaps": vocabulary_gaps,
        "recovered_concatenated_rows": [
            {
                "line_number": row.line_number,
                "raw_text": row.raw_line,
                "temperature_as_published": row.temperature_token,
                "heat_capacity_as_published": row.cells["heat_capacity"].token,
                "label": row.label,
                "assigned_segment": f"segment-{segment_index(row)}",
            }
            for row in short
            if row.recovered_concatenated_temperature_cp
        ],
        "refused_concatenated_rows": refused_concatenated_rows,
        "refused_layout_rows": refused_layout_rows,
        "non_data_marker_lines": non_data_marker_lines,
        "stored_cell_errata": applied_cell_errata,
        "merged_formation_rows": [
            {
                "temperature_as_published": row.temperature_token,
                "line_number": row.line_number,
                "raw_text": row.tail_text,
                "assigned_segment": f"segment-{segment_index(row)}",
                "values_as_published": {
                    column: cell.token for column, cell in row.numeric_tail.items()
                },
                "cell_dispositions": {
                    column: (
                        {"disposition": "stored_zero"}
                        if cell.value == 0
                        else {
                            "disposition": "refused",
                            "reason": MERGED_FORMATION_REFUSAL_REASON,
                        }
                    )
                    for column, cell in row.numeric_tail.items()
                },
            }
            for row in short
            if row.numeric_tail is not None
        ],
    }
    return TableGeneration(tuple(observations), report)


def observations_from_table(
    payload: Mapping[str, Any],
    *,
    source_path: str | None = None,
) -> list[Observation]:
    """Return only the schema-v2.1 observations for one parsed JANAF table."""

    return list(generate_table(payload, source_path=source_path).observations)


@dataclass(frozen=True)
class JANAFFusionEnergy:
    delta_g_fus_kJ_per_mol: Decimal
    melting_temperature_K: Decimal
    accepted_melting_temperature_K: Decimal
    crystal_table: str
    liquid_table: str
    source_sha256: tuple[str, str]


_FUSION_TABLES = {
    "CaO": ("Ca-027", "Ca-028"),
    "Al2O3": ("Al-096", "Al-100"),
    # Mg-009 is the MgO(l) Gibbs function; like Al-100 and Ca-028, its
    # observation series is phase-unknown around JANAF's GLASS <--> LIQUID row.
    "MgO": ("Mg-008", "Mg-009"),
    # High cristobalite is the high-temperature solid branch immediately
    # below the accepted SiO2 melting point; the quartz table is metastable.
    "SiO2": ("O-035", "O-038"),
}
# The source hashes identify the JANAF text; pin the extracted temperature-node
# sets too so a dropped compiled row cannot silently widen interpolation.
_FUSION_NODE_SET_SHA256 = {
    "Al-096": "86986e6bc3d75c0fec4c3a034a1deab91e966c42d5e0f1cb4080a33d10aa6bed",
    "Al-100": "b61dfea8f1da3f3a90ba7c7ceedc7b1cd04048c46fb4cf20bf5e5c3c4f0c0b18",
    "Ca-027": "0b4adb5cf7b86a053c456688e9e635947ca38276f7b703cb00062ca5f00dbb5c",
    "Ca-028": "4a9048a895bd8b3e499110d27edbbbe984c8c130881687ee8654a2f750afb765",
    "Mg-008": "b61dfea8f1da3f3a90ba7c7ceedc7b1cd04048c46fb4cf20bf5e5c3c4f0c0b18",
    "Mg-009": "e7c4becbfe7033b2b739a3b12f1404808d4fa05c33c4ee04d15cc1e5c114d8d4",
    "O-035": "86986e6bc3d75c0fec4c3a034a1deab91e966c42d5e0f1cb4080a33d10aa6bed",
    "O-038": "4a9048a895bd8b3e499110d27edbbbe984c8c130881687ee8654a2f750afb765",
}
_ACCEPTED_MELTING_K = {
    "CaO": Decimal("2886"),
    "Al2O3": Decimal("2327"),
    "MgO": Decimal("3100"),
    "SiO2": Decimal("1986"),
}


@lru_cache(maxsize=2 * len(_FUSION_TABLES))
def _fusion_table_points(table_id: str) -> tuple[tuple[tuple[Decimal, Decimal], ...], str]:
    path = TABLES_DIR / f"{table_id}.yaml"
    document = load_table_document(path)
    extraction = document.get("extraction")
    table = document.get("table")
    if not isinstance(extraction, Mapping) or not isinstance(table, Mapping):
        raise ValueError(f"{table_id}: JANAF cached table is incomplete")
    digest = str(extraction.get("source_sha256") or "")
    expected = _sidecar_hashes().get(f"{table_id}.txt")
    if not digest or digest != expected:
        raise ValueError(f"{table_id}: JANAF cache hash does not match source sidecar")
    rows = table.get("values")
    if not isinstance(rows, list):
        raise ValueError(f"{table_id}: JANAF table values are missing")
    points: list[tuple[Decimal, Decimal]] = []
    temperatures: set[Decimal] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        temperature = row.get("temperature")
        gibbs = row.get("formation_gibbs_energy")
        if not isinstance(temperature, Mapping):
            continue
        t_value = temperature.get("value")
        if t_value is None:
            continue
        t = Decimal(str(t_value))
        temperatures.add(t)
        g_value = gibbs.get("value") if isinstance(gibbs, Mapping) else None
        if g_value is not None:
            points.append((t, Decimal(str(g_value))))
    node_set_sha256 = hashlib.sha256(
        "\n".join(
            str(temperature.normalize()) for temperature in sorted(temperatures)
        ).encode()
    ).hexdigest()
    expected_node_set_sha256 = _FUSION_NODE_SET_SHA256.get(table_id)
    if (
        expected_node_set_sha256 is not None
        and node_set_sha256 != expected_node_set_sha256
    ):
        raise ValueError(f"{table_id}: needed JANAF table node row missing or changed")
    points.sort()
    if len(points) < 2:
        raise ValueError(f"{table_id}: JANAF table has fewer than two Gibbs points")
    return tuple(points), digest


@lru_cache(maxsize=2 * len(_FUSION_TABLES))
def _fusion_missing_gibbs_temperatures(table_id: str) -> tuple[Decimal, ...]:
    """Return table nodes with a temperature but no formation-Gibbs value."""

    document = load_table_document(TABLES_DIR / f"{table_id}.yaml")
    table = document.get("table")
    if not isinstance(table, Mapping):
        raise ValueError(f"{table_id}: JANAF cached table is incomplete")
    rows = table.get("values")
    if not isinstance(rows, list):
        raise ValueError(f"{table_id}: JANAF table values are missing")
    missing: set[Decimal] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        temperature = row.get("temperature")
        gibbs = row.get("formation_gibbs_energy")
        if not isinstance(temperature, Mapping):
            continue
        temperature_value = temperature.get("value")
        gibbs_value = gibbs.get("value") if isinstance(gibbs, Mapping) else None
        if temperature_value is not None and gibbs_value is None:
            missing.add(Decimal(str(temperature_value)))
    return tuple(sorted(missing))


def _interpolate_formation_gibbs(
    points: tuple[tuple[Decimal, Decimal], ...], temperature_K: Decimal
) -> Decimal:
    if temperature_K < points[0][0] or temperature_K > points[-1][0]:
        raise ValueError(f"{temperature_K} K is outside the JANAF table range")
    missing_node = _missing_node_for_interpolation(points, (), temperature_K)
    if missing_node is not None:
        raise ValueError(
            f"JANAF formation Gibbs interpolation spans missing grid node "
            f"{missing_node} K at {temperature_K} K"
        )
    for (t0, g0), (t1, g1) in zip(points, points[1:]):
        if t0 <= temperature_K <= t1:
            if temperature_K == t0:
                return g0
            if temperature_K == t1:
                return g1
            return g0 + (g1 - g0) * (temperature_K - t0) / (t1 - t0)
    raise ValueError(f"{temperature_K} K is not bracketed by JANAF table rows")


def _missing_node_for_interpolation(
    points: tuple[tuple[Decimal, Decimal], ...],
    missing_nodes: tuple[Decimal, ...],
    temperature_K: Decimal,
) -> Decimal | None:
    if any(node_temperature == temperature_K for node_temperature, _ in points):
        return None
    temperatures = tuple(temperature for temperature, _ in points)
    gaps = tuple(right - left for left, right in zip(temperatures, temperatures[1:]))
    for gap_index, ((left, _), (right, _)) in enumerate(zip(points, points[1:])):
        if left < temperature_K < right:
            absent_grid_nodes = tuple(
                Decimal(candidate)
                for candidate in range(
                    int(left // 100) * 100 + 100,
                    int(right // 100) * 100 + 1,
                    100,
                )
                if left < Decimal(candidate) < right
            )
            for node in sorted(
                {node for node in missing_nodes if left < node < right}
                | set(absent_grid_nodes)
            ):
                run_start = gap_index
                run_end = gap_index + 1
                while run_start > 0 and gaps[run_start - 1] in (
                    Decimal("200"),
                    Decimal("400"),
                ):
                    run_start -= 1
                while run_end < len(gaps) and gaps[run_end] in (
                    Decimal("200"),
                    Decimal("400"),
                ):
                    run_end += 1
                sparse_ladder = (
                    left >= Decimal("3000")
                    and right - left in (Decimal("200"), Decimal("400"))
                    and run_end - run_start >= 2
                )
                reference_interval = (
                    left == Decimal("298.15") and right == Decimal("500")
                )
                if not sparse_ladder and not reference_interval:
                    return node
            return None
    return None


def janaf_fusion_energy(oxide: str, temperature_K: Decimal) -> JANAFFusionEnergy:
    """Read ΔG_fus and its own cr/l table crossing from hash-checked JANAF rows."""

    try:
        crystal_table, liquid_table = _FUSION_TABLES[oxide]
        accepted_tm = _ACCEPTED_MELTING_K[oxide]
    except KeyError as exc:
        raise ValueError(f"no JANAF fusion table pair for {oxide}") from exc
    temperature_K = Decimal(str(temperature_K))
    crystal, crystal_sha = _fusion_table_points(crystal_table)
    liquid, liquid_sha = _fusion_table_points(liquid_table)
    overlap = sorted(
        {t for t, _ in crystal if liquid[0][0] <= t <= liquid[-1][0]}
        | {t for t, _ in liquid if crystal[0][0] <= t <= crystal[-1][0]}
    )
    if len(overlap) < 2:
        raise ValueError(f"{oxide}: JANAF crystal/liquid tables do not overlap")
    crystal_missing = _fusion_missing_gibbs_temperatures(crystal_table)
    liquid_missing = _fusion_missing_gibbs_temperatures(liquid_table)
    overlap = [
        temperature
        for temperature in overlap
        if _missing_node_for_interpolation(
            crystal, crystal_missing, temperature
        ) is None
        and _missing_node_for_interpolation(liquid, liquid_missing, temperature) is None
    ]
    if len(overlap) < 2:
        raise ValueError(f"{oxide}: JANAF tables have fewer than two complete overlap nodes")

    def difference(t: Decimal) -> Decimal:
        return _interpolate_formation_gibbs(
            liquid, t
        ) - _interpolate_formation_gibbs(crystal, t)

    crossings: list[Decimal] = []
    for left, right in zip(overlap, overlap[1:]):
        d_left, d_right = difference(left), difference(right)
        if d_left == 0:
            crossings.append(left)
        elif d_left * d_right < 0:
            crossings.append(left - d_left * (right - left) / (d_right - d_left))
    if overlap and difference(overlap[-1]) == 0:
        crossings.append(overlap[-1])
    if len(crossings) != 1:
        raise ValueError(f"{oxide}: expected one JANAF cr/l crossing, got {crossings}")
    tm = crossings[0]
    for table_id, points, missing_nodes in (
        (crystal_table, crystal, crystal_missing),
        (liquid_table, liquid, liquid_missing),
    ):
        for interpolation_temperature in (temperature_K, tm):
            missing_node = _missing_node_for_interpolation(
                points, missing_nodes, interpolation_temperature
            )
            if missing_node is not None:
                raise ValueError(
                    f"{table_id}: needed JANAF formation Gibbs row missing at "
                    f"{missing_node} K for interpolation at "
                    f"{interpolation_temperature} K"
                )
    try:
        delta_g = difference(temperature_K)
    except ValueError as exc:
        raise ValueError(
            f"{oxide}: {exc}; JANAF table ranges: "
            f"{crystal_table} [{crystal[0][0]}, {crystal[-1][0]}] K; "
            f"{liquid_table} [{liquid[0][0]}, {liquid[-1][0]}] K"
        ) from exc
    return JANAFFusionEnergy(
        delta_g_fus_kJ_per_mol=delta_g,
        melting_temperature_K=tm,
        accepted_melting_temperature_K=accepted_tm,
        crystal_table=crystal_table,
        liquid_table=liquid_table,
        source_sha256=(crystal_sha, liquid_sha),
    )


def _sidecar_hashes(path: Path = SIDECAR_PATH) -> dict[str, str]:
    payload = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or not isinstance(payload.get("files"), list):
        raise ValueError(f"{path}: invalid JANAF source sidecar")
    result: dict[str, str] = {}
    for row in payload["files"]:
        if isinstance(row, Mapping) and row.get("name") and row.get("sha256"):
            result[str(row["name"])] = str(row["sha256"])
    return result


def documents_from_raw(directory: Path) -> Iterable[Mapping[str, Any]]:
    """Hash-verify and parse raw files through the harvester's parser."""

    expected = _sidecar_hashes()
    paths = sorted(directory.glob("*.txt"))
    if not paths:
        raise ValueError(f"{directory}: no .txt files")
    for path in paths:
        wanted = expected.get(path.name)
        if wanted is None:
            raise ValueError(f"{path}: absent from JANAF source sidecar")
        raw = path.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if actual != wanted:
            raise ValueError(f"{path}: sha256 mismatch: expected {wanted}, got {actual}")
        table_id = path.stem
        yield parse_table(
            raw,
            {
                "table_id": table_id,
                "url": f"https://janaf.nist.gov/tables/{table_id}.html",
                "download_url": f"https://janaf.nist.gov/tables/{table_id}.txt",
                "name": "",
            },
            path,
        )


def documents_from_tables(directory: Path) -> Iterable[Mapping[str, Any]]:
    paths = sorted(directory.glob("*.yaml"))
    if not paths:
        raise ValueError(f"{directory}: no .yaml files")
    for path in paths:
        yield load_table_document(path)


def write_staging(documents: Iterable[Mapping[str, Any]], out: Path) -> Mapping[str, Any]:
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out}: staging directory must be empty")
    out.mkdir(parents=True, exist_ok=True)
    observation_shards: dict[str, list[object]] = defaultdict(list)
    report_shards: dict[str, list[object]] = defaultdict(list)
    source_shards: dict[str, list[str]] = defaultdict(list)
    quantity_counts: Counter[str] = Counter()
    point_counts: Counter[str] = Counter()
    phase_segment_counts: Counter[str] = Counter()
    polymorph_segment_counts: Counter[str] = Counter()
    phase_observation_counts: dict[str, Counter[str]] = defaultdict(Counter)
    cell_totals: dict[str, Counter[str]] = defaultdict(Counter)
    transcription_checks: Counter[str] = Counter()
    failure_counts: Counter[str] = Counter()
    stored_pair_identity_failure_count = 0
    stored_pair_identity_denominator: Counter[str] = Counter()
    refused_merged_pair_check_count = 0
    refused_merged_pair_identity_failure_count = 0
    raw_numeric_accounting: Counter[str] = Counter()
    recovered_concatenated_row_count = 0
    refused_concatenated_row_count = 0
    refused_layout_row_count = 0
    non_data_marker_line_count = 0
    adjacent_condition_counts: Counter[str] = Counter()
    merged_formation_row_count = 0
    table_count = 0
    started = time.monotonic()
    last_progress = started
    for table_count, document in enumerate(documents, start=1):
        table = _unwrap_table(document)
        table_id = str(table.get("table_id") or "")
        source_path = f"{TABLE_SOURCE_PREFIX}/{table_id}.yaml"
        generated = generate_table(document, source_path=source_path)
        shard = table_id.split("-", 1)[0]
        observation_shards[shard].extend(to_plain(obs) for obs in generated.observations)
        report_shards[shard].append(to_plain(generated.report))
        source_shards[shard].append(source_path)
        for obs in generated.observations:
            quantity = obs.identity.quantity.value
            quantity_counts[quantity.value] += 1
            point_counts[quantity.value] += len(obs.value.series or ())
            if obs.value.point is not None:
                point_counts[quantity.value] += 1
        for segment in generated.report["phase_segments"]:
            phase = segment["phase"]
            phase_key = phase.get("value") if phase.get("tag") == "value" else "unknown"
            phase_segment_counts[str(phase_key)] += 1
            polymorph = segment["polymorph"]
            polymorph_key = (
                polymorph.get("value")
                if polymorph.get("tag") == "value"
                else polymorph.get("tag")
            )
            polymorph_segment_counts[str(polymorph_key)] += 1
            for quantity, count in segment["observation_counts"].items():
                phase_observation_counts[str(phase_key)][quantity] += int(count)
        for column, row in generated.report["cell_accounting"].items():
            for key in (
                "structured_numeric",
                "short_row_numeric",
                "numeric_source_cells",
                "stored_points",
                "printed_null_cells",
                "excluded_numeric_total",
                "unexplained_numeric",
            ):
                cell_totals[column][key] += int(row[key])
        for failure in generated.report["transcription_identity_failures"]:
            failure_counts[str(failure["identity"])] += 1
        stored_pair_identity_failure_count += len(
            generated.report["stored_pair_identity_failures"]
        )
        stored_pair_identity_denominator.update(
            generated.report.get("stored_pair_identity_denominator") or {}
        )
        refused_merged_pair_check_count += int(
            generated.report["refused_merged_pair_checks"]
        )
        refused_merged_pair_identity_failure_count += len(
            generated.report["refused_merged_pair_identity_failures"]
        )
        raw_numeric_accounting.update(generated.report["raw_numeric_accounting"])
        recovered_concatenated_row_count += len(
            generated.report["recovered_concatenated_rows"]
        )
        refused_concatenated_row_count += len(
            generated.report["refused_concatenated_rows"]
        )
        refused_layout_row_count += len(
            generated.report.get("refused_layout_rows") or ()
        )
        non_data_marker_line_count += len(
            generated.report.get("non_data_marker_lines") or ()
        )
        adjacent_condition_counts.update(
            str(row["label"]) for row in generated.report["adjacent_condition_rows"]
        )
        merged_formation_row_count += len(generated.report["merged_formation_rows"])
        transcription_checks.update(generated.report["transcription_checks"])
        now = time.monotonic()
        if table_count % 100 == 0 or now - last_progress >= 60:
            print(f"JANAF generator: {table_count} tables in {now - started:.1f}s", flush=True)
            last_progress = now
    if table_count == 0:
        raise ValueError("no JANAF documents supplied")
    observations_dir = out / "observations"
    reports_dir = out / "reports"
    for shard in sorted(observation_shards):
        dump_yaml(
            {
                "schema_version": "battery_observations.v2.1",
                "source_id": SOURCE_ID,
                "sources": source_shards[shard],
                "observations": observation_shards[shard],
            },
            observations_dir / f"janaf-{shard}.yaml",
        )
        dump_yaml(
            {
                "schema_version": "janaf_generator_report.v1",
                "source_id": SOURCE_ID,
                "tables": report_shards[shard],
            },
            reports_dir / f"janaf-{shard}.yaml",
        )
    summary = {
        "schema_version": "janaf_generator_summary.v1",
        "source_id": SOURCE_ID,
        "table_count": table_count,
        "observation_counts_by_quantity": dict(sorted(quantity_counts.items())),
        "point_counts_by_quantity": dict(sorted(point_counts.items())),
        "phase_segment_counts": dict(sorted(phase_segment_counts.items())),
        "polymorph_segment_counts": dict(sorted(polymorph_segment_counts.items())),
        "observation_counts_by_phase": {
            phase: dict(sorted(counts.items()))
            for phase, counts in sorted(phase_observation_counts.items())
        },
        "cell_accounting": {
            column: dict(sorted(counts.items()))
            for column, counts in sorted(cell_totals.items())
        },
        "transcription_check_counts": dict(sorted(transcription_checks.items())),
        "transcription_identity_failure_counts": dict(sorted(failure_counts.items())),
        "transcription_gas_constant_J_per_mol_K": str(JANAF_R_J_PER_MOL_K),
        "stored_pair_identity_failure_count": stored_pair_identity_failure_count,
        "stored_pair_identity_denominator": dict(
            sorted(stored_pair_identity_denominator.items())
        ),
        "refused_merged_pair_check_count": refused_merged_pair_check_count,
        "refused_merged_pair_identity_failure_count": (
            refused_merged_pair_identity_failure_count
        ),
        "raw_numeric_accounting": dict(sorted(raw_numeric_accounting.items())),
        "recovered_concatenated_row_count": recovered_concatenated_row_count,
        "refused_concatenated_row_count": refused_concatenated_row_count,
        "refused_layout_row_count": refused_layout_row_count,
        "non_data_marker_line_count": non_data_marker_line_count,
        "merged_formation_row_count": merged_formation_row_count,
        "adjacent_condition_row_counts": dict(sorted(adjacent_condition_counts.items())),
        "sharding": "one observation shard and one report shard per JANAF index element",
    }
    dump_yaml(summary, out / "summary.yaml")
    print(f"JANAF generator: wrote {table_count} tables to {out}", flush=True)
    return summary


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--raw", type=Path, help="directory of hash-verified NIST .txt files")
    source.add_argument("--tables", type=Path, help="directory of harvested tables/*.yaml files")
    parser.add_argument("--out", type=Path, required=True, help="empty staging directory")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    documents = documents_from_raw(args.raw) if args.raw else documents_from_tables(args.tables)
    write_staging(documents, args.out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
