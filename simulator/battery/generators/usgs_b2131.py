"""USGS Bulletin 2131 observations the generic compilation path cannot unit-map.

High-temperature pages print one header: Cp, S, (H−H298)/T and −(G−H298)/T in
J·mol⁻¹·K⁻¹, and ΔfH and ΔfG in kJ·mol⁻¹. The captured units_as_published
string is one garbled line, so the generic converter drops every energy and
heat column. Log Kf already imports. This generator stores the numeric Cp, S,
ΔfH, ΔfG and log Kf cells under that printed header and leaves the two /T
functions unstored.

A phase sentence is split on its printed temperature bounds. A temperature
that falls in two adjacent windows is the closing row, then the opening row.
A temperature outside every window is not assigned to a neighbour.

Reference-state pages (298.15 K, or the printed 291.15 K grid) put the value
on the first numeric line of a column and the uncertainty on the next numeric
line. Those parents are the migrator's "1 numeric" and "2 numeric" formation
Gibbs groups. The second line is not a second formation energy.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from simulator.battery.enums import (
    QUANTITY_UNITS,
    EvidenceClass,
    PerBasis,
    Phase,
    Quantity,
    UncertaintyKind,
    ValueKind,
)
from simulator.battery.generators.compilation_formula import aligned_raster_formula
from simulator.battery.generators.usgs_b1544 import RecordGeneration
from simulator.battery.generators.usgs_b2131_raster import FORMULA_RASTER, PHASE_SENTENCE
from simulator.battery.migrate import (
    _NO_ADMISSION_STATUS_REASON,
    _defaulted_admission,
    compilation_source_basis,
    fill_identity,
    make_species,
    map_phase,
)
from simulator.battery.records import (
    Derivation,
    Evidence,
    Located,
    Locator,
    Observation,
    State,
    Uncertainty,
    Value,
)

SOURCE_ID = "robie-hemingway-1995-usgs-b2131"
_PREFIX = f"data/literature/compilations/{SOURCE_ID}"

# Printed high-temperature columns. enthalpy_function and gibbs_function are
# (H−H298)/T and −(G−H298)/T, not H(T)−H(298).
_HT_COLUMNS = {
    "cp": Quantity.CP,
    "entropy": Quantity.S,
    "formation_enthalpy": Quantity.DELTA_FH,
    "formation_gibbs": Quantity.DELTA_FG,
    "log_kf": Quantity.LOG10_KF,
}
_REFERENCE_COLUMNS = {
    "entropy": Quantity.S,
    "formation_enthalpy": Quantity.DELTA_FH,
    "formation_gibbs": Quantity.DELTA_FG,
    "log_kf": Quantity.LOG10_KF,
}
_MOLAR_HEADER = frozenset(
    {Quantity.CP, Quantity.S, Quantity.DELTA_FH, Quantity.DELTA_FG}
)
_SUFFIX = {
    Quantity.CP: "cp",
    Quantity.S: "S:entropy",
    Quantity.DELTA_FH: "delta_fH",
    Quantity.DELTA_FG: "delta_fG",
    Quantity.LOG10_KF: "log10_Kf",
}
_PLAIN_DECIMAL = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)")
_PHASE_RANGE = re.compile(
    r"(\d+(?:\.\d+)?)\.?\s*(?:K|X|It)?\s*(?:to|–|—|-)\s+"
    r"(?:[A-Za-z~']+\s+){0,4}"
    r"(\d+(?:\.\d+)?)\s*(?:K|X|It)\b",
    re.IGNORECASE,
)
_CRYSTAL_WORD = re.compile(
    r"crystal|cubic|rhombohedral|hexagonal|orthorhombic|monoclinic|"
    r"triclinic|tetragonal|\bsolid\b",
    re.IGNORECASE,
)
_LIQUID_WORD = re.compile(r"\bliquid\b", re.IGNORECASE)
_GAS_WORD = re.compile(r"\bgas\b", re.IGNORECASE)

_HT_RELATION = (
    "USGS Bulletin 2131 high-temperature header: Cp and S in J·mol-1·K-1; "
    "ΔfH and ΔfG in kJ·mol-1; log Kf dimensionless. "
    "(H−H298)/T and −(G−H298)/T are printed per kelvin and are not stored. "
    "Captured units_as_published is not the unit source."
)
_REFERENCE_RELATION = (
    "USGS Bulletin 2131 reference-state header: S in J·mol-1·K-1; "
    "ΔfH and ΔfG in kJ·mol-1; log(K) dimensionless. "
    "The next numeric line in a column is the printed uncertainty, not a "
    "second temperature."
)
# MinerU shows SrCO3 and KAlSi3O8. Same-length case / 0→O / 1→l cannot
# insert the C, and it cannot turn the 9 into 8. Emitting the OCR token
# would store a different stoichiometry, and the generic path would do the
# same for log Kf, so these two records produce no observations.
_OFF_NUMERIC_PATH = frozenset(
    {
        "high-temperature-p274",
        "high-temperature-p367",
    }
)


@dataclass(frozen=True)
class _Window:
    index: int
    lo: Decimal
    hi: Decimal
    label: str


def _decimal_cell(cell: object) -> tuple[Decimal, str, bool] | None:
    if not isinstance(cell, Mapping):
        return None
    raw = cell.get("raw")
    suspect = bool(cell.get("ocr_suspect"))
    if (
        isinstance(raw, str)
        and _PLAIN_DECIMAL.fullmatch(raw.strip())
        and not (suspect and cell.get("value") is not None)
    ):
        return Decimal(raw.strip()), raw.strip(), suspect
    value = cell.get("value")
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    if not amount.is_finite():
        return None
    token = str(raw).strip() if raw not in (None, "") else str(value)
    return amount, token, suspect


def _published_formula(doc: Mapping[str, Any]) -> str:
    return str(doc.get("formula_as_published") or doc.get("formula") or "")


def formula_for_record(doc: Mapping[str, Any]) -> tuple[str, str | None]:
    """Stored formula, plus the raster quote when a correction is applied.

    The table is re-checked with the alignment rule. A row that is not an
    allowed case or digit-to-element edit is ignored.
    """

    published = _published_formula(doc)
    row = FORMULA_RASTER.get(str(doc.get("record_id") or ""))
    if row is None or row[0] != published:
        return published, None
    corrected = aligned_raster_formula(published, row[1])
    if corrected != row[1]:
        return published, None
    return corrected, row[2]


def phase_windows(text: str) -> tuple[_Window, ...]:
    """Printed temperature windows, in sentence order."""

    windows: list[_Window] = []
    previous = 0
    for match in _PHASE_RANGE.finditer(text or ""):
        lo = Decimal(match.group(1))
        hi = Decimal(match.group(2))
        if hi < lo:
            previous = match.end()
            continue
        label = (text or "")[previous:match.start()]
        label = " ".join(label.split())
        if ":" in label:
            label = label.split(":")[-1].strip()
        label = label.strip(" .;,-")
        windows.append(_Window(len(windows), lo, hi, label))
        previous = match.end()
    return tuple(windows)


def _phase_of_label(label: str) -> State[Phase]:
    crystal = _CRYSTAL_WORD.search(label) is not None
    liquid = _LIQUID_WORD.search(label) is not None
    gas = _GAS_WORD.search(label) is not None
    hits = [
        phase
        for phase, flag in ((Phase.CR, crystal), (Phase.L, liquid), (Phase.G, gas))
        if flag
    ]
    if len(hits) != 1:
        shown = label.strip() or "empty window label"
        return State.unknown(
            f"phase window {shown!r} does not name one of crystal, liquid, gas"
        )
    return State.of(hits[0])


def _assign_windows(
    temperatures: list[Decimal], windows: tuple[_Window, ...]
) -> list[_Window | None]:
    """First of a duplicate boundary temperature closes; the next opens.

    A temperature outside every window stays unassigned.
    """

    seen: dict[Decimal, int] = defaultdict(int)
    assigned: list[_Window | None] = []
    for temperature in temperatures:
        hits = [window for window in windows if window.lo <= temperature <= window.hi]
        occurrence = seen[temperature]
        seen[temperature] += 1
        if len(hits) == 1:
            assigned.append(hits[0])
        elif len(hits) > 1 and occurrence < len(hits):
            assigned.append(hits[occurrence])
        else:
            assigned.append(None)
    return assigned


def _pages(doc: Mapping[str, Any]) -> tuple[int | None, int | None]:
    printed = doc.get("page")
    pdf = doc.get("pdf_page")
    printed_page = printed if isinstance(printed, int) and not isinstance(printed, bool) else None
    pdf_page = pdf if isinstance(pdf, int) and not isinstance(pdf, bool) else None
    return printed_page, pdf_page


def _locator(
    doc: Mapping[str, Any],
    source_path: str,
    note: str,
) -> Locator:
    printed, pdf = _pages(doc)
    record_id = str(doc.get("record_id") or "")
    return Locator(
        published_page=printed,
        pdf_page_index=pdf,
        table=record_id,
        source_path=source_path,
        record=record_id,
        note=note,
    )


def _species(formula: str, phase: Phase | State[Phase]):
    return make_species(formula, phase)


def _basis_kwargs(
    *,
    formula: str,
    phase: State[Phase],
    quantity: Quantity,
    value: Value,
    temperature_K: State[Decimal],
) -> dict[str, State]:
    basis = compilation_source_basis(
        source_id=SOURCE_ID,
        formula=formula,
        phase=phase,
        quantity=quantity,
        value=value,
        temperature_K=temperature_K,
    )
    known: dict[str, State] = {}
    if "per" in basis:
        known["per"] = basis["per"]
    elif quantity in _MOLAR_HEADER:
        known["per"] = State.of(PerBasis.MOL_SPECIES)
    if "standard_pressure_Pa" in basis:
        known["standard_pressure_Pa"] = basis["standard_pressure_Pa"]
    return known


def _one_observation(
    *,
    doc: Mapping[str, Any],
    source_path: str,
    formula: str,
    quantity: Quantity,
    value: Value,
    phase: State[Phase],
    temperature_K: State[Decimal],
    suffix: str,
    note: str,
    relation: str,
    point_temperature: Decimal | None,
) -> Observation:
    record_id = str(doc["record_id"])
    species = _species(formula, phase)
    known = _basis_kwargs(
        formula=formula,
        phase=phase,
        quantity=quantity,
        value=value,
        temperature_K=temperature_K,
    )
    known["temperature_K"] = temperature_K
    identity = fill_identity(State.of(quantity), species, **known)
    locator = _locator(doc, source_path, note)
    point_conditions = None
    if point_temperature is not None:
        point_conditions = {
            "temperature_K": Located(State.of(point_temperature), locator=locator)
        }
    return Observation(
        observation_id=f"{SOURCE_ID}:{record_id}:{suffix}",
        experiment_id=f"{SOURCE_ID}:{record_id}:tabulation",
        identity=identity,
        value=value,
        uncertainty=Uncertainty(kind=UncertaintyKind.NONE),
        evidence=Evidence(
            class_=State.of(EvidenceClass.COMPILATION_ASSESSED),
            original_method_class="compilation",
            model="compilation",
        ),
        admission=_defaulted_admission(_NO_ADMISSION_STATUS_REASON),
        notices=(),
        source_id=SOURCE_ID,
        locator=locator,
        read_from=source_path,
        point_conditions=point_conditions,
        derivation=Derivation(
            relation=relation,
            inputs=(source_path,),
            parameters=(),
            output_unit=QUANTITY_UNITS[quantity],
        ),
    )


def _formula_note(published: str, formula: str, quote: str | None) -> str:
    if quote is None or formula == published:
        return ""
    clipped = " ".join(quote.split())
    if len(clipped) > 180:
        clipped = clipped[:177] + "..."
    return f"formula_as_published={published!r}; raster={formula!r}; mineru={clipped!r}"


def _ht_rows(
    doc: Mapping[str, Any],
) -> list[tuple[Decimal, dict[Quantity, tuple[Decimal, bool]]]]:
    rows: list[tuple[Decimal, dict[Quantity, tuple[Decimal, bool]]]] = []
    for row in doc.get("rows") or []:
        if not isinstance(row, Mapping):
            continue
        cells = row.get("cells")
        if not isinstance(cells, Mapping):
            continue
        temperature = _decimal_cell(cells.get("temperature"))
        if temperature is None:
            continue
        values: dict[Quantity, tuple[Decimal, bool]] = {}
        for column, quantity in _HT_COLUMNS.items():
            parsed = _decimal_cell(cells.get(column))
            if parsed is not None:
                values[quantity] = (parsed[0], parsed[2])
        rows.append((temperature[0], values))
    return rows


def _generate_ht(doc: Mapping[str, Any]) -> RecordGeneration:
    record_id = str(doc["record_id"])
    source_path = f"{_PREFIX}/records/{record_id}.json"
    if record_id in _OFF_NUMERIC_PATH:
        return RecordGeneration(
            observations=(),
            report={"record_id": record_id, "skipped": "raster alignment cannot reach the printed formula"},
        )
    published = _published_formula(doc)
    formula, quote = formula_for_record(doc)
    if not formula:
        return RecordGeneration(
            observations=(),
            report={"record_id": record_id, "skipped": "blank formula"},
        )
    sentence = PHASE_SENTENCE.get(record_id) or str(doc.get("phase_as_published") or "")
    windows = phase_windows(sentence)
    parsed_rows = _ht_rows(doc)
    temperatures = [temperature for temperature, _values in parsed_rows]
    assigned = _assign_windows(temperatures, windows) if windows else [None] * len(parsed_rows)
    formula_note = _formula_note(published, formula, quote)
    # quantity -> window index or None -> points
    buckets: dict[tuple[Quantity, int | None], list[tuple[Decimal, Decimal, bool]]] = defaultdict(
        list
    )
    for (temperature, values), window in zip(parsed_rows, assigned):
        key_window = None if not windows else (None if window is None else window.index)
        if not windows:
            key_window = None
        for quantity, (amount, suspect) in values.items():
            buckets[(quantity, key_window)].append((temperature, amount, suspect))
    observations: list[Observation] = []
    split = len(windows) > 1
    for (quantity, window_index), points in buckets.items():
        if not points:
            continue
        if not windows:
            phase = map_phase(sentence)[0]
            label = "phase sentence has no printed temperature bounds"
            suffix = _SUFFIX[quantity]
        elif window_index is None:
            phase = State.unknown(
                "temperature is outside every printed phase-window bound"
            )
            label = "outside printed phase windows"
            suffix = f"{_SUFFIX[quantity]}:uncovered"
        else:
            window = windows[window_index]
            phase = _phase_of_label(window.label)
            label = (
                f"{window.label} {window.lo} to {window.hi} K"
                if window.label
                else f"{window.lo} to {window.hi} K"
            )
            suffix = f"{_SUFFIX[quantity]}:w{window.index}" if split else _SUFFIX[quantity]
        suspect_n = sum(1 for _t, _v, suspect in points if suspect)
        note = "; ".join(
            part
            for part in (
                formula_note,
                label,
                f"ocr_suspect cells={suspect_n}" if suspect_n else "",
            )
            if part
        )
        series = tuple((temperature, amount) for temperature, amount, _suspect in points)
        temperature_state = State.unknown(
            f"source prints a temperature grid ({_SUFFIX[quantity]}); "
            "series coordinate, not a single identity temperature_K"
        )
        observations.append(
            _one_observation(
                doc=doc,
                source_path=source_path,
                formula=formula,
                quantity=quantity,
                value=Value(ValueKind.SERIES, series=series),
                phase=phase,
                temperature_K=temperature_state,
                suffix=suffix,
                note=note,
                relation=_HT_RELATION,
                point_temperature=None,
            )
        )
    return RecordGeneration(
        observations=tuple(observations),
        report={"record_id": record_id, "windows": len(windows), "observations": len(observations)},
    )


def _grid_temperature(doc: Mapping[str, Any]) -> Decimal | None:
    grid = doc.get("temperature_grid")
    if not isinstance(grid, list) or len(grid) != 1 or not isinstance(grid[0], Mapping):
        return None
    parsed = _decimal_cell(grid[0])
    if parsed is not None:
        return parsed[0]
    return None


def _column_value_and_uncertainty(
    doc: Mapping[str, Any], column: str
) -> tuple[tuple[Decimal | None, str, bool], tuple[Decimal, str, bool] | None] | None:
    rows = [
        row
        for row in doc.get("rows") or []
        if isinstance(row, Mapping) and isinstance(row.get("cells"), Mapping)
    ]
    if rows:
        first_cell = rows[0]["cells"].get(column)
        if (
            isinstance(first_cell, Mapping)
            and first_cell.get("value") is None
            and str(first_cell.get("raw") or "").strip()
        ):
            uncertainty = next(
                (
                    parsed
                    for row in rows[1:]
                    if (parsed := _decimal_cell(row["cells"].get(column))) is not None
                ),
                None,
            )
            if uncertainty is not None:
                return (
                    (
                        None,
                        str(first_cell["raw"]).strip(),
                        bool(first_cell.get("ocr_suspect")),
                    ),
                    uncertainty,
                )
    found: list[tuple[Decimal, str, bool]] = []
    for row in rows:
        cells = row["cells"]
        parsed = _decimal_cell(cells.get(column))
        if parsed is None:
            continue
        found.append(parsed)
        if len(found) == 2:
            break
    if not found:
        return None
    return found[0], (found[1] if len(found) > 1 else None)


def _generate_reference(doc: Mapping[str, Any]) -> RecordGeneration:
    record_id = str(doc["record_id"])
    source_path = f"{_PREFIX}/summary/{record_id}.json"
    temperature = _grid_temperature(doc)
    if temperature is None:
        return RecordGeneration(observations=(), report={"record_id": record_id, "skipped": "no single grid temperature"})
    if record_id in _OFF_NUMERIC_PATH:
        return RecordGeneration(
            observations=(),
            report={"record_id": record_id, "skipped": "raster alignment cannot reach the printed formula"},
        )
    published = _published_formula(doc)
    formula, quote = formula_for_record(doc)
    if not formula:
        return RecordGeneration(
            observations=(),
            report={"record_id": record_id, "skipped": "blank formula"},
        )
    phase_raw = doc.get("phase_as_published")
    if isinstance(phase_raw, list):
        phase_raw = " ".join(str(part) for part in phase_raw)
    phase = map_phase(phase_raw)[0]
    formula_note = _formula_note(published, formula, quote)
    observations: list[Observation] = []
    for column, quantity in _REFERENCE_COLUMNS.items():
        pair = _column_value_and_uncertainty(doc, column)
        if pair is None:
            continue
        (amount, token, suspect), uncertainty = pair
        bits = [
            formula_note,
            f"T={temperature} K from temperature_grid",
            f"raw={token}",
        ]
        if uncertainty is not None:
            bits.append(f"printed uncertainty raw={uncertainty[1]}")
        if suspect:
            bits.append("ocr_suspect")
        value = (
            Value.point_of(amount)
            if amount is not None
            else Value(
                kind=ValueKind.UNAVAILABLE,
                unavailable_reason=(
                    f"no parsed numeric value for {column}; printed token "
                    f"{token!r} is unparsed and the following numeric line is uncertainty"
                ),
            )
        )
        uncertainty_obj = Uncertainty(kind=UncertaintyKind.NONE)
        if uncertainty is not None:
            uncertainty_obj = Uncertainty(
                kind=UncertaintyKind.PRINTED,
                verbatim=uncertainty[1],
                value=uncertainty[0],
                basis=(
                    "reference-state table prints this figure on the line "
                    "under the value"
                ),
            )
        observation = _one_observation(
            doc=doc,
            source_path=source_path,
            formula=formula,
            quantity=quantity,
            value=value,
            phase=phase,
            temperature_K=State.of(temperature),
            suffix=quantity.value,
            note="; ".join(bit for bit in bits if bit),
            relation=_REFERENCE_RELATION,
            point_temperature=temperature,
        )
        if uncertainty_obj.kind is not UncertaintyKind.NONE:
            observation = replace(observation, uncertainty=uncertainty_obj)
        observations.append(observation)
    return RecordGeneration(
        observations=tuple(observations),
        report={"record_id": record_id, "observations": len(observations)},
    )


def owns_record(doc: Mapping[str, Any]) -> bool:
    """Records this generator replaces. Others stay on the generic path.

    A high-temperature column key with no parsed number stays on the generic
    path, so an all-null table still records its refusal.
    """

    if str(doc.get("source_id") or "") != SOURCE_ID or not doc.get("record_id"):
        return False
    # An empty heading is not a formula. The record id must not replace it.
    if not _published_formula(doc).strip():
        return False
    kind = str(doc.get("table_kind") or "")
    if kind == "high_temperature":
        for row in doc.get("rows") or []:
            if not isinstance(row, Mapping):
                continue
            cells = row.get("cells")
            if not isinstance(cells, Mapping):
                continue
            for column in _HT_COLUMNS:
                if _decimal_cell(cells.get(column)) is not None:
                    return True
        return False
    if kind == "reference_state_298K" and _grid_temperature(doc) is not None:
        for column in ("formation_gibbs", "log_kf"):
            if _column_value_and_uncertainty(doc, column) is not None:
                return True
    return False


def generate_record(payload: Mapping[str, Any]) -> RecordGeneration:
    kind = str(payload.get("table_kind") or "")
    if kind == "high_temperature":
        return _generate_ht(payload)
    if kind == "reference_state_298K":
        return _generate_reference(payload)
    return RecordGeneration(observations=(), report={})
