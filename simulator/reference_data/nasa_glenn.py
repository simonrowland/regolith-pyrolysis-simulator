"""NASA Glenn / CEA ``thermo.inp`` compilation loader.

Parses the CEA fixed-column species format (McBride, Zehe and Gordon,
NASA/TP-2002-211556; CEA ``thermo.inp``) without unit conversion,
smoothing, phase merging, or silent repair. Parse ambiguities are
recorded on the record and in the compilation manifest.

On-disk home: ``data/literature/compilations/nasa-glenn/``.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPILATION_ROOT = ROOT / "data" / "literature" / "compilations" / "nasa-glenn"
SOURCE_REL = "data/literature/compilations/nasa-glenn/source/thermo.inp"
SCHEMA_VERSION = "literature_compilation.v1"
SOURCE_ID = "nasa-glenn"
MANIFEST_SCHEMA = "literature_compilation_manifest.v1"

# IUPAC element symbols that appear as CEA 1- or 2-character formula tokens.
# D/T are hydrogen isotopes used as element codes in thermo.inp; E is the
# electron. Inert locked-species codes (IO, IH, …) are NOT in this set.
ELEMENT_SYMBOLS = frozenset(
    {
        "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne",
        "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar", "K", "Ca",
        "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
        "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb", "Sr", "Y", "Zr",
        "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn",
        "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd",
        "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb",
        "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
        "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac", "Th",
        "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm",
        "D", "T", "E",
    }
)

# Keys in feedstocks.yaml that are mixture labels, not chemical formulas.
_NON_FORMULA_FEEDSTOCK_KEYS = frozenset(
    {
        "carbonaceous_organic",
        "carbonate_salts",
        "organics",
        "CH4_NH3_HCN",
        "CO_CO2",
    }
)
_FORMULA_KEY_RE = re.compile(r"^(?:[A-Z][a-z]?\d*)+$")
_FORMULA_TOKEN_RE = re.compile(r"([A-Z][a-z]?)(\d*)")
_FLOAT_TOKEN_RE = re.compile(
    r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[DdEe][+-]?\d+)?"
)
_PHASE_SUFFIX_RE = re.compile(r"\(([^)]+)\)(?:,.*)?$")
_PHASE_TAGS = frozenset(
    {
        "cr",
        "L",
        "l",
        "a",
        "b",
        "c",
        "d",
        "I",
        "II",
        "III",
        "IV",
        "gr",
        "s",
        "S",
        "g",
        "alpha",
        "beta",
        "gamma",
        "liq",
        "ref",
        "am",
    }
)
_GAS_PHASE_TAGS = frozenset({"g", "G", "gas"})

COMPILATION_SOURCE = {
    "database": "NASA Glenn / CEA thermo.inp",
    "citation": (
        "McBride, B. J., Zehe, M. J. and Gordon, S., NASA Glenn Coefficients "
        "for Calculating Thermodynamic Properties of Individual Species, "
        "NASA/TP-2002-211556; NASA CEA thermo.inp."
    ),
    "version": "thermo.inp dated 9/8/2021 (Snyder CEA standalone updates)",
    "official_url": "https://www.grc.nasa.gov/www/CEAWeb/",
    "licence": (
        "US government work; 17 U.S.C. § 105 (no U.S. copyright). "
        "Public-domain basis."
    ),
}

COMPILATION_ROLE = {
    "kind": "assessed_thermodynamic_functions",
    "engine_reference_input": True,
    "validation_measurement": False,
    "scoring_eligible": False,
    "battery_refusal": "gibbs_table_not_runtime_observable",
    "circularity_warning": (
        "Do not validate an engine against a compilation it consumes."
    ),
}


class NasaGlennParseError(ValueError):
    """Unrecoverable CEA fixed-column parse defect."""


@dataclass
class PublishedNumber:
    """A number retained as the published token plus its parsed float."""

    as_published: str
    value: float | None

    def to_dict(self) -> dict[str, Any]:
        return {"as_published": self.as_published, "value": self.value}


@dataclass
class CompositionSlot:
    element_as_published: str
    count_as_published: str
    element: str | None
    count: float | None
    filler: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "element_as_published": self.element_as_published,
            "count_as_published": self.count_as_published,
            "element": self.element,
            "count": self.count,
            "filler": self.filler,
        }


@dataclass
class IntervalRecord:
    T_min_K: PublishedNumber
    T_max_K: PublishedNumber
    n_poly_terms: PublishedNumber
    exponents: list[PublishedNumber]
    H298_minus_H0: PublishedNumber
    a_coefficients: list[PublishedNumber]
    a8_as_published: str
    b1: PublishedNumber | None
    b2: PublishedNumber | None
    raw_t_line: str
    raw_coeff_lines: list[str]
    ambiguities: list[dict[str, Any]] = field(default_factory=list)

    @property
    def coefficient_count(self) -> int:
        n = sum(1 for c in self.a_coefficients if c.as_published.strip())
        if self.b1 is not None and self.b1.as_published.strip():
            n += 1
        if self.b2 is not None and self.b2.as_published.strip():
            n += 1
        return n

    def to_dict(self) -> dict[str, Any]:
        return {
            "T_min_K": self.T_min_K.to_dict(),
            "T_max_K": self.T_max_K.to_dict(),
            "n_poly_terms": self.n_poly_terms.to_dict(),
            "exponents": [item.to_dict() for item in self.exponents],
            "H298_minus_H0": self.H298_minus_H0.to_dict(),
            "a_coefficients": [item.to_dict() for item in self.a_coefficients],
            "a8_as_published": self.a8_as_published,
            "b1": None if self.b1 is None else self.b1.to_dict(),
            "b2": None if self.b2 is None else self.b2.to_dict(),
            "coefficient_count": self.coefficient_count,
            "raw_t_line": self.raw_t_line,
            "raw_coeff_lines": list(self.raw_coeff_lines),
            "ambiguities": list(self.ambiguities),
        }


@dataclass
class SpeciesRecord:
    record_id: str
    name_as_published: str
    citation_as_published: str
    cea_section: str
    n_intervals_declared: int
    source_ref_code: str
    composition_slots: list[CompositionSlot]
    formula: str
    phase_flag: int | None
    phase_flag_as_published: str
    phase: str
    molecular_weight: PublishedNumber
    delta_f_H_298_15: PublishedNumber
    intervals: list[IntervalRecord]
    raw_name_line: str
    raw_header_line: str
    source_path: str
    name_line_number: int
    header_line_number: int
    end_line_number: int
    ambiguities: list[dict[str, Any]] = field(default_factory=list)

    @property
    def coefficient_count(self) -> int:
        return sum(iv.coefficient_count for iv in self.intervals)

    @property
    def interval_count(self) -> int:
        return len(self.intervals)

    def elements(self) -> tuple[str, ...]:
        seen: list[str] = []
        for slot in self.composition_slots:
            if slot.filler or not slot.element:
                continue
            if slot.element not in seen:
                seen.append(slot.element)
        return tuple(seen)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "source_id": SOURCE_ID,
            "source": dict(COMPILATION_SOURCE),
            "compilation_role": dict(COMPILATION_ROLE),
            "record_id": self.record_id,
            "name_as_published": self.name_as_published,
            "citation_as_published": self.citation_as_published,
            "cea_section": self.cea_section,
            "formula": self.formula,
            "phase": self.phase,
            "phase_flag": self.phase_flag,
            "phase_flag_as_published": self.phase_flag_as_published,
            "n_intervals_declared": self.n_intervals_declared,
            "source_ref_code": self.source_ref_code,
            "composition": [slot.to_dict() for slot in self.composition_slots],
            "molecular_weight": self.molecular_weight.to_dict(),
            "delta_f_H_298_15": {
                **self.delta_f_H_298_15.to_dict(),
                "units_as_published": "J/mol",
            },
            "intervals": [iv.to_dict() for iv in self.intervals],
            "coefficient_count": self.coefficient_count,
            "interval_count": self.interval_count,
            "ambiguity_count": len(self.ambiguities),
            "ambiguities": list(self.ambiguities),
            "source_locator": {
                "path": self.source_path,
                "name_line": self.name_line_number,
                "header_line": self.header_line_number,
                "end_line": self.end_line_number,
            },
            "source_text": {
                "name_line": self.raw_name_line,
                "header_line": self.raw_header_line,
                "interval_lines": _interval_source_lines(self.intervals),
            },
        }


@dataclass
class ThermoInpFile:
    records: list[SpeciesRecord]
    default_t_line: str
    default_t_line_number: int
    source_path: str
    source_sha256: str


def _interval_source_lines(intervals: Iterable[IntervalRecord]) -> list[str]:
    lines: list[str] = []
    for iv in intervals:
        lines.append(iv.raw_t_line)
        lines.extend(iv.raw_coeff_lines)
    return lines


def parse_fortran_float(token: str) -> float:
    text = token.strip().replace("D", "E").replace("d", "e")
    if not text:
        raise NasaGlennParseError("empty Fortran float token")
    return float(text)


def _published(token: str) -> PublishedNumber:
    stripped = token.strip()
    if not stripped:
        return PublishedNumber(as_published=token, value=None)
    return PublishedNumber(as_published=stripped, value=parse_fortran_float(stripped))


def _col(line: str, start: int, end: int) -> str:
    """0-based half-open slice; short lines behave as space-padded."""
    if len(line) < end:
        line = line + " " * (end - len(line))
    return line[start:end]


def _is_species_name_line(line: str) -> bool:
    if not line or line.startswith("!") or line[0] in " \t":
        return False
    stripped = line.strip().upper()
    if stripped.startswith("END"):
        return False
    if stripped == "THERMO":
        return False
    return True


def _normalize_element_symbol(token: str) -> str | None:
    raw = token.strip()
    if not raw:
        return None
    if not raw.isalpha():
        return raw
    if len(raw) == 1:
        candidate = raw.upper()
    else:
        candidate = raw[0].upper() + raw[1:].lower()
    if candidate in ELEMENT_SYMBOLS:
        return candidate
    # CEA inert locked-species codes (IO, IH, IC) stay as published.
    return raw


def _formula_from_slots(slots: list[CompositionSlot]) -> str:
    parts: list[str] = []
    for slot in slots:
        if slot.filler or not slot.element:
            continue
        count = slot.count
        if count is None:
            parts.append(slot.element)
            continue
        if abs(count - round(count)) < 1e-12:
            n = int(round(count))
            if n == 0:
                continue
            parts.append(slot.element if n == 1 else f"{slot.element}{n}")
        else:
            parts.append(f"{slot.element}{slot.count_as_published.strip()}")
    return "".join(parts) if parts else ""


def _phase_from_name(name: str) -> str | None:
    match = _PHASE_SUFFIX_RE.search(name.strip())
    if not match:
        return None
    tag = match.group(1)
    if tag in _PHASE_TAGS:
        return tag
    return None


def _phase_label(name: str, phase_flag: int | None) -> str:
    suffix = _phase_from_name(name)
    if suffix:
        return suffix
    if phase_flag == 0:
        return "gas"
    if phase_flag is None:
        return "not_parsed"
    return "condensed"


def _parse_composition_slots(header_line: str) -> tuple[list[CompositionSlot], list[dict[str, Any]]]:
    ambiguities: list[dict[str, Any]] = []
    slots: list[CompositionSlot] = []
    block = _col(header_line, 10, 50)
    for index in range(5):
        chunk = block[index * 8 : (index + 1) * 8]
        element_raw = chunk[0:2]
        count_raw = chunk[2:8]
        element = _normalize_element_symbol(element_raw)
        count: float | None
        try:
            count = parse_fortran_float(count_raw) if count_raw.strip() else None
        except ValueError:
            count = None
            ambiguities.append(
                {
                    "kind": "composition_count_unparsed",
                    "slot": index,
                    "element_as_published": element_raw,
                    "count_as_published": count_raw,
                }
            )
        filler = (not element_raw.strip()) and (count is None or abs(count) < 1e-15)
        if element and element not in ELEMENT_SYMBOLS and not filler:
            ambiguities.append(
                {
                    "kind": "nonstandard_element_token",
                    "slot": index,
                    "element_as_published": element_raw,
                    "normalized": element,
                }
            )
        if count is not None and not filler and abs(count - round(count)) > 1e-12:
            ambiguities.append(
                {
                    "kind": "non_integer_atom_count",
                    "slot": index,
                    "element_as_published": element_raw,
                    "count_as_published": count_raw,
                }
            )
        slots.append(
            CompositionSlot(
                element_as_published=element_raw,
                count_as_published=count_raw,
                element=element,
                count=count,
                filler=filler,
            )
        )
    return slots, ambiguities


def _parse_header_mw_hf(header_line: str) -> tuple[int | None, str, PublishedNumber, PublishedNumber, list[dict[str, Any]]]:
    """Parse phase flag (cols 51-52), MW (53-65), Hf (66-80).

    The electron record prints MW across the phase/MW boundary
    (``0.000548579903``). That overlap is recorded, not repaired.
    """
    ambiguities: list[dict[str, Any]] = []
    phase_raw = _col(header_line, 50, 52)
    mw_raw = _col(header_line, 52, 65)
    hf_raw = _col(header_line, 65, 80)
    tail = _col(header_line, 50, 80)
    phase_flag: int | None = None
    if phase_raw.strip().isdigit():
        phase_flag = int(phase_raw.strip())
    mw: PublishedNumber
    hf: PublishedNumber
    try:
        mw = _published(mw_raw)
        hf = _published(hf_raw)
    except ValueError:
        mw = PublishedNumber(as_published=mw_raw, value=None)
        hf = PublishedNumber(as_published=hf_raw, value=None)
    mw_looks_broken = (
        mw_raw.strip().startswith(".")
        or mw.value is None
        or hf.value is None
        or (hf.as_published and not _FLOAT_TOKEN_RE.fullmatch(hf.as_published.replace("D", "E").replace("d", "e")))
    )
    # Electron / other overlap: standard columns do not yield a clean Hf float.
    if mw_looks_broken or (hf.value is not None and abs(hf.value) > 1e20):
        floats = [_published(tok) for tok in _FLOAT_TOKEN_RE.findall(tail)]
        if len(floats) >= 2:
            ambiguities.append(
                {
                    "kind": "phase_mw_column_overlap",
                    "phase_as_published": phase_raw,
                    "mw_field_as_published": mw_raw,
                    "hf_field_as_published": hf_raw,
                    "tail_as_published": tail.rstrip(),
                    "note": (
                        "Fixed columns 51-80 do not split cleanly; "
                        "MW and Hf taken from free-scanned tail floats. "
                        "Not silently repaired: both the column fields and "
                        "the free-scan values are retained."
                    ),
                }
            )
            # Keep column tokens as published; fill .value from the tail.
            mw = PublishedNumber(as_published=floats[-2].as_published, value=floats[-2].value)
            hf = PublishedNumber(as_published=floats[-1].as_published, value=floats[-1].value)
            if phase_flag is None:
                phase_flag = 0
        elif mw.value is None or hf.value is None:
            raise NasaGlennParseError(
                f"unparseable phase/MW/Hf tail: {tail!r}"
            )
    if phase_flag is None:
        ambiguities.append(
            {
                "kind": "phase_flag_unparsed",
                "phase_as_published": phase_raw,
            }
        )
    return phase_flag, phase_raw, mw, hf, ambiguities


def parse_header_line(header_line: str) -> dict[str, Any]:
    nint_raw = _col(header_line, 0, 2)
    ref_raw = _col(header_line, 3, 9)
    try:
        n_intervals = int(nint_raw.strip() or "0")
    except ValueError as exc:
        raise NasaGlennParseError(
            f"unparseable interval count {nint_raw!r} in {header_line!r}"
        ) from exc
    slots, slot_ambiguities = _parse_composition_slots(header_line)
    phase_flag, phase_raw, mw, hf, tail_ambiguities = _parse_header_mw_hf(header_line)
    return {
        "n_intervals": n_intervals,
        "n_intervals_as_published": nint_raw,
        "source_ref_code": ref_raw.strip(),
        "source_ref_code_as_published": ref_raw,
        "composition_slots": slots,
        "phase_flag": phase_flag,
        "phase_flag_as_published": phase_raw,
        "molecular_weight": mw,
        "delta_f_H_298_15": hf,
        "ambiguities": slot_ambiguities + tail_ambiguities,
    }


def _parse_coeff_fields(line: str) -> list[str]:
    padded = _col(line, 0, 80)
    return [padded[i : i + 16] for i in range(0, 80, 16)]


def parse_interval_block(
    t_line: str,
    coeff_lines: list[str],
    *,
    assigned_enthalpy: bool,
) -> IntervalRecord:
    ambiguities: list[dict[str, Any]] = []
    t_min = _published(_col(t_line, 0, 11))
    t_max = _published(_col(t_line, 11, 22))
    n_poly = _published(_col(t_line, 22, 23))
    exponents = [_published(_col(t_line, 23 + 5 * i, 28 + 5 * i)) for i in range(8)]
    h0 = _published(_col(t_line, 65, 80))
    a_coeffs: list[PublishedNumber] = []
    a8 = ""
    b1: PublishedNumber | None = None
    b2: PublishedNumber | None = None
    if assigned_enthalpy:
        ambiguities.append(
            {
                "kind": "assigned_enthalpy_no_polynomial",
                "note": (
                    "nint=0 reactant/product: dummy T-line only, no "
                    "coefficient lines. T_max is 0 as published."
                ),
            }
        )
    else:
        if len(coeff_lines) != 2:
            raise NasaGlennParseError(
                f"expected 2 coefficient lines, got {len(coeff_lines)}"
            )
        fields1 = _parse_coeff_fields(coeff_lines[0])
        fields2 = _parse_coeff_fields(coeff_lines[1])
        a_coeffs = [_published(tok) for tok in fields1] + [
            _published(fields2[0]),
            _published(fields2[1]),
        ]
        a8 = fields2[2]
        b1 = _published(fields2[3])
        b2 = _published(fields2[4])
        if n_poly.value not in (None, 7.0):
            ambiguities.append(
                {
                    "kind": "n_poly_not_7",
                    "n_poly_as_published": n_poly.as_published,
                }
            )
    if (
        t_min.value is not None
        and t_max.value is not None
        and not (t_min.value < t_max.value)
    ):
        ambiguities.append(
            {
                "kind": "inverted_or_zero_width_T_interval",
                "T_min_K_as_published": t_min.as_published,
                "T_max_K_as_published": t_max.as_published,
                "note": "Published T bounds retained; interval not dropped.",
            }
        )
    return IntervalRecord(
        T_min_K=t_min,
        T_max_K=t_max,
        n_poly_terms=n_poly,
        exponents=exponents,
        H298_minus_H0=h0,
        a_coefficients=a_coeffs,
        a8_as_published=a8,
        b1=b1,
        b2=b2,
        raw_t_line=t_line,
        raw_coeff_lines=list(coeff_lines),
        ambiguities=ambiguities,
    )


def parse_thermo_inp(
    text: str,
    *,
    source_path: str = SOURCE_REL,
    source_sha256: str = "",
) -> ThermoInpFile:
    """Parse a full CEA ``thermo.inp`` into one record per species/phase.

    Walks both the PRODUCTS and REACTANTS sections. ``nint=0`` assigned-
    enthalpy rows are kept (dummy T-line, no coefficients). Inverted
    T-intervals are kept and flagged. Nothing is merged or dropped.
    """
    lines = text.splitlines()
    i = 0
    while i < len(lines) and lines[i].strip().lower() != "thermo":
        i += 1
    if i >= len(lines):
        raise NasaGlennParseError("thermo.inp missing 'thermo' marker")
    i += 1
    default_t_line = ""
    default_t_line_number = 0
    if i < len(lines) and not _is_species_name_line(lines[i]):
        default_t_line = lines[i]
        default_t_line_number = i + 1
        i += 1

    records: list[SpeciesRecord] = []
    section = "products"
    while i < len(lines):
        line = lines[i]
        stripped = line.strip().upper()
        if stripped.startswith("END"):
            if "REACT" in stripped:
                break
            if "PRODUCT" in stripped:
                section = "reactants"
            i += 1
            continue
        if line.startswith("!") or not line.strip():
            i += 1
            continue
        if not _is_species_name_line(line):
            raise NasaGlennParseError(
                f"line {i + 1}: expected species name, got {line!r}"
            )
        if i + 1 >= len(lines):
            raise NasaGlennParseError(f"line {i + 1}: truncated record (no header)")
        name_line_number = i + 1
        header_line_number = i + 2
        name = line[:18].rstrip() if len(line) >= 18 else line.strip()
        citation = line[18:].rstrip() if len(line) > 18 else ""
        header = parse_header_line(lines[i + 1])
        nint = int(header["n_intervals"])
        cursor = i + 2
        intervals: list[IntervalRecord] = []
        if nint == 0:
            if cursor >= len(lines):
                raise NasaGlennParseError(f"{name}: nint=0 truncated dummy T-line")
            intervals.append(
                parse_interval_block(
                    lines[cursor],
                    [],
                    assigned_enthalpy=True,
                )
            )
            cursor += 1
        else:
            for _ in range(nint):
                if cursor + 2 >= len(lines):
                    raise NasaGlennParseError(
                        f"{name}: truncated interval block at line {cursor + 1}"
                    )
                intervals.append(
                    parse_interval_block(
                        lines[cursor],
                        [lines[cursor + 1], lines[cursor + 2]],
                        assigned_enthalpy=False,
                    )
                )
                cursor += 3
        ambiguities: list[dict[str, Any]] = list(header["ambiguities"])
        for iv_index, iv in enumerate(intervals):
            for item in iv.ambiguities:
                ambiguities.append({"interval_index": iv_index, **item})
        if len(name) == 18:
            ambiguities.append(
                {
                    "kind": "name_occupies_full_18_column_field",
                    "name_as_published": name,
                    "note": "CEA name field is cols 1-18; no trailing pad in this record.",
                }
            )
        phase_flag = header["phase_flag"]
        suffix = _phase_from_name(name)
        if suffix and phase_flag == 0 and suffix not in _GAS_PHASE_TAGS:
            ambiguities.append(
                {
                    "kind": "phase_flag_zero_with_condensed_name_suffix",
                    "phase_flag": phase_flag,
                    "name_suffix": suffix,
                }
            )
        if (not suffix) and phase_flag not in (0, None) and "(" not in name:
            ambiguities.append(
                {
                    "kind": "condensed_phase_flag_without_name_suffix",
                    "phase_flag": phase_flag,
                    "name_as_published": name,
                }
            )
        record = SpeciesRecord(
            record_id="",
            name_as_published=name,
            citation_as_published=citation,
            cea_section=section,
            n_intervals_declared=nint,
            source_ref_code=str(header["source_ref_code"]),
            composition_slots=list(header["composition_slots"]),
            formula=_formula_from_slots(list(header["composition_slots"])),
            phase_flag=phase_flag,
            phase_flag_as_published=str(header["phase_flag_as_published"]),
            phase=_phase_label(name, phase_flag),
            molecular_weight=header["molecular_weight"],
            delta_f_H_298_15=header["delta_f_H_298_15"],
            intervals=intervals,
            raw_name_line=line,
            raw_header_line=lines[i + 1],
            source_path=source_path,
            name_line_number=name_line_number,
            header_line_number=header_line_number,
            end_line_number=cursor,
            ambiguities=ambiguities,
        )
        records.append(record)
        i = cursor

    name_counts: Counter[str] = Counter(rec.name_as_published for rec in records)
    by_name: dict[str, list[int]] = defaultdict(list)
    for index, rec in enumerate(records):
        rec.record_id = f"NG-{index + 1:04d}"
        by_name[rec.name_as_published].append(index)
    for name, indices in by_name.items():
        if len(indices) < 2:
            continue
        ids = [records[j].record_id for j in indices]
        for j in indices:
            records[j].ambiguities.append(
                {
                    "kind": "duplicate_name_as_published",
                    "name_as_published": name,
                    "record_ids": ids,
                    "note": "Distinct thermo.inp records; not merged.",
                }
            )

    return ThermoInpFile(
        records=records,
        default_t_line=default_t_line,
        default_t_line_number=default_t_line_number,
        source_path=source_path,
        source_sha256=source_sha256,
    )


def record_document(record: SpeciesRecord) -> dict[str, Any]:
    return record.to_dict()


def load_record_document(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    manifest_path = path or (COMPILATION_ROOT / "manifest.yaml")
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise NasaGlennParseError(f"{manifest_path}: manifest is not a mapping")
    return payload


def iter_record_paths(records_dir: Path | None = None) -> Iterator[Path]:
    root = records_dir or (COMPILATION_ROOT / "records")
    yield from sorted(root.glob("NG-*.json"))


def load_all_record_documents(
    records_dir: Path | None = None,
) -> list[dict[str, Any]]:
    return [load_record_document(path) for path in iter_record_paths(records_dir)]


def published_float_pairs(record_doc: Mapping[str, Any]) -> list[tuple[str, float | None]]:
    """Flatten every published numeric token in a record document."""
    pairs: list[tuple[str, float | None]] = []

    def walk(node: Any, prefix: str) -> None:
        if isinstance(node, Mapping):
            if "as_published" in node and "value" in node:
                pairs.append((prefix, node.get("value")))
                return
            for key, child in node.items():
                walk(child, f"{prefix}.{key}" if prefix else str(key))
            return
        if isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{prefix}[{index}]")

    walk(record_doc.get("molecular_weight"), "molecular_weight")
    walk(record_doc.get("delta_f_H_298_15"), "delta_f_H_298_15")
    walk(record_doc.get("intervals"), "intervals")
    for index, slot in enumerate(record_doc.get("composition") or []):
        if isinstance(slot, Mapping) and slot.get("count") is not None:
            pairs.append((f"composition[{index}].count", slot.get("count")))
    return pairs


_FEEDSTOCK_COMPOSITION_SECTIONS = (
    "composition_wt_pct",
    "non_oxide_components",
    "bulk_additions",
    "structural_water",
    "trace_elements",
    "solar_wind_volatiles",
)


def feedstock_element_symbols(feedstocks_path: Path | None = None) -> list[str]:
    """Element symbols declared in ``data/feedstocks.yaml`` compositions."""
    path = feedstocks_path or (ROOT / "data" / "feedstocks.yaml")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise NasaGlennParseError(f"{path}: expected mapping")
    found: set[str] = set()

    def add_formula(token: str) -> None:
        raw = token.strip()
        raw = re.sub(r"_(ppm|wt_pct|kg_per_tonne)$", "", raw)
        if raw in _NON_FORMULA_FEEDSTOCK_KEYS:
            if raw == "CH4_NH3_HCN":
                found.update({"C", "H", "N"})
            elif raw == "CO_CO2":
                found.update({"C", "O"})
            return
        if raw in ELEMENT_SYMBOLS:
            found.add(raw)
            return
        if _FORMULA_KEY_RE.fullmatch(raw):
            for element, _count in _FORMULA_TOKEN_RE.findall(raw):
                found.add(element)

    def walk_mapping(node: Mapping[str, Any]) -> None:
        for key, child in node.items():
            add_formula(str(key))
            if isinstance(child, Mapping):
                walk_mapping(child)

    for entry in payload.values():
        if not isinstance(entry, Mapping):
            continue
        for section in _FEEDSTOCK_COMPOSITION_SECTIONS:
            body = entry.get(section)
            if isinstance(body, Mapping):
                walk_mapping(body)
    return sorted(found)


def coverage_by_element(
    records: Iterable[Mapping[str, Any] | SpeciesRecord],
    elements: Iterable[str],
) -> dict[str, dict[str, Any]]:
    present: dict[str, list[str]] = defaultdict(list)
    for rec in records:
        if isinstance(rec, SpeciesRecord):
            rec_id = rec.record_id
            rec_elements = rec.elements()
        else:
            rec_id = str(rec.get("record_id") or "")
            rec_elements = tuple(
                str(slot.get("element"))
                for slot in (rec.get("composition") or [])
                if not slot.get("filler") and slot.get("element")
            )
        for element in rec_elements:
            present[element].append(rec_id)
    table: dict[str, dict[str, Any]] = {}
    for element in elements:
        ids = present.get(element, [])
        table[element] = {
            "element": element,
            "has_record": bool(ids),
            "record_count": len(ids),
            "record_ids_head": ids[:8],
        }
    return table
