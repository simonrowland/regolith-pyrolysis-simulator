#!/usr/bin/env python3
"""t-425 / VR-4 — NASA CEA ``thermo.inp`` ingester.

Parse cited CEA / NASA-9 records, **preserve** source coefficients, segment
bounds, standard-state conventions, citations, and validation status, and emit
REV5 four-strata **DRAFT** rows (``enabled_for_merge: false``).

Runtime never refits spreadsheet rows from this tool. Antoine / residual
spreadsheet fits are fixture-only and are **not** written as production catalog
authority. No production ``data/vapor_pressures.yaml`` row is enabled here.

Usage examples::

  python tools/vp_cea_ingest.py \\
      --thermo tests/fixtures/cea/thermo_subset.inp \\
      --output tests/fixtures/cea/vp-cea-rows-DRAFT.yaml

  python tools/vp_cea_ingest.py \\
      --thermo tests/fixtures/cea/thermo_subset.inp \\
      --volatile-draft docs-private/research/2026-07-25-trace-vp-refs/vp-volatile-rows-DRAFT.yaml \\
      --output tests/fixtures/cea/vp-cea-volatile-trial-DRAFT.yaml
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.vapour_rail.nasa_cea import (  # noqa: E402
    NASA9_DEFAULT_EXPONENTS,
    Nasa9Segment,
    NasaCeaConventionError,
    NasaCeaError,
    NasaCeaPolynomial,
    NasaCeaSegmentError,
    StandardState,
)

# ---------------------------------------------------------------------------
# thermo.inp parsing (NASA-9 / Glenn fixed free-text records)
# ---------------------------------------------------------------------------

_FLOAT_TOKEN = re.compile(
    r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[DdEe][+-]?\d+)?"
)


def _parse_fortran_float(token: str) -> float:
    return float(token.replace("D", "E").replace("d", "e"))


def _parse_float_tokens(line: str) -> list[float]:
    """Parse all Fortran/C float tokens from a thermo.inp line."""
    return [_parse_fortran_float(t) for t in _FLOAT_TOKEN.findall(line)]


def _parse_coeff_line_16(line: str) -> list[float]:
    """Parse a NASA coefficient line (fields of width 16, optional leading space)."""
    s = line.rstrip("\n")
    # thermo.inp coeff lines are typically packed as 5×16-char fields, often
    # with a single leading space. Free-token parse is robust to blank holes
    # used before b1/b2 on the second coeff line.
    return _parse_float_tokens(s)


def _is_species_name_line(line: str) -> bool:
    if not line or line.startswith("!") or line[0] in " \t":
        return False
    if line.strip().upper().startswith("END"):
        return False
    if line.strip().lower() == "thermo":
        return False
    return True


def _phase_flag_to_standard_state(phase: int, name: str) -> StandardState:
    """Map CEA phase integer to a typed standard-state convention.

    CEA: 0 = gas; nonzero = condensed. Condensed names commonly carry
    ``(cr)``, ``(L)``, ``(a)``…; we preserve that distinction when present.
    """
    if phase == 0:
        return "gas"
    upper = name.upper()
    if "(L)" in name or name.endswith("(L)") or "(L)" in upper:
        return "condensed_liquid"
    if any(tag in name for tag in ("(cr)", "(a)", "(b)", "(c)", "(d)", "(s)")):
        return "condensed_solid"
    return "condensed"


@dataclass(frozen=True)
class CeaSpeciesRecord:
    """One preserved CEA species/phase record (source coefficients intact)."""

    name: str
    citation: str
    n_intervals: int
    source_ref_code: str
    formula_tokens: list[str]
    phase_flag: int
    standard_state: StandardState
    molecular_weight_g_per_mol: float
    delta_f_H_298_15_J_per_mol: float
    intervals: list[dict[str, Any]]
    raw_header_line: str
    raw_name_line: str
    # Provenance when --skip-invalid-segments drops inverted/zero-width rows:
    # source_n_intervals is the header-declared count; n_intervals is retained.
    source_n_intervals: int = 0
    dropped_inverted_segments: int = 0

    def to_polynomial(self) -> NasaCeaPolynomial:
        segments: list[Nasa9Segment] = []
        for iv in self.intervals:
            coeffs = tuple(iv["a_coefficients"])
            if len(coeffs) != 7:
                raise NasaCeaSegmentError(
                    f"{self.name}: expected 7 a-coefficients, got {len(coeffs)}"
                )
            segments.append(
                Nasa9Segment(
                    T_min_K=float(iv["T_min_K"]),
                    T_max_K=float(iv["T_max_K"]),
                    coefficients=coeffs,  # type: ignore[arg-type]
                    b1=float(iv["b1"]),
                    b2=float(iv["b2"]),
                    exponents=tuple(iv.get("exponents", NASA9_DEFAULT_EXPONENTS)),
                )
            )
        formula = _formula_from_tokens(self.formula_tokens)
        return NasaCeaPolynomial(
            name=self.name,
            family="nasa_cea_9",
            standard_state=self.standard_state,
            segments=tuple(segments),
            formula=formula,
            molecular_weight_g_per_mol=self.molecular_weight_g_per_mol,
            delta_f_H_298_15_J_per_mol=self.delta_f_H_298_15_J_per_mol,
            citation=self.citation,
            source_ref_code=self.source_ref_code,
        )


def _normalize_element_symbol(token: str) -> str:
    """CEA header tokens are uppercase (NA, FE, SIO→SI+O split already).

    Element symbols are case-sensitive in chemical formulas and atom maps.
    Normalize to IUPAC form: first letter upper, remainder lower (Na, Fe, Si).
    Single-letter elements (H, C, O, N, …) stay single uppercase.
    """
    raw = str(token).strip()
    if not raw:
        return raw
    # Only alphabetic element tokens are normalized; leave counts/digits alone.
    if not raw.isalpha():
        return raw
    return raw[0].upper() + raw[1:].lower()


def _formula_from_tokens(tokens: Sequence[str]) -> str:
    """Join CEA element/count tokens like ['H', '2.00', 'O', '1.00'] → 'H2O'.

    Element symbols are normalized to IUPAC case (NA→Na, FE→Fe, SIO→SiO)
    so case-sensitive atom maps do not break on naive transcription.
    """
    parts: list[str] = []
    i = 0
    while i < len(tokens):
        el = _normalize_element_symbol(tokens[i])
        if i + 1 < len(tokens):
            try:
                count = float(tokens[i + 1])
                if abs(count - round(count)) < 1e-9:
                    n = int(round(count))
                    parts.append(el if n == 1 else f"{el}{n}")
                else:
                    parts.append(f"{el}{count:g}")
                i += 2
                continue
            except ValueError:
                pass
        parts.append(el)
        i += 1
    return "".join(parts)


def _parse_header_line(line: str) -> dict[str, Any]:
    """Parse the CEA species header (n_int, ref, formula, phase, MW, Hf)."""
    # Format (TP / CAP):
    #  cols roughly: nint(2) ref(~6-8) date  elements×5 (el 2 + count 6) phase MW Hf
    # Free-parse with structure awareness.
    s = line.rstrip()
    nint = int(s[0:2].strip() or s.split()[0])
    rest = s[2:].lstrip()
    # Reference tokens must survive byte-faithfully (review: g10/97 must not
    # become g10). Three source spellings appear in thermo.inp:
    #   glued month-in-letter:  "g10/97", "g12/97", "g11/99", "j12/66"
    #   spaced letter + m/yy:   "g 8/89", "j 3/78", "n 4/83"
    #   word + yy:              "srd 01", "srd 93", "bar 89"
    #   bare alphanumeric:      "tpis89", "coda89"
    m = re.match(
        r"(?P<ref>"
        r"[A-Za-z]+\d+/\d{2}"  # g10/97
        r"|[A-Za-z]+\s+\d{1,2}/\d{2}"  # g 8/89
        r"|[A-Za-z]+\s+\d{2}"  # srd 01 / bar 89
        r"|[A-Za-z]+[0-9]*"  # tpis89 / bare g11
        r")\s*(?P<body>.*)$",
        rest,
    )
    if not m:
        raise NasaCeaConventionError(f"unparseable CEA header line: {line!r}")
    ref = m.group("ref")
    # Collapse interior whitespace runs in spaced forms ("g  8/89" → "g 8/89")
    # without altering glued slash-year tokens.
    ref = re.sub(r"\s+", " ", ref.strip())
    body = m.group("body")
    # Trailing: phase (int) MW Hf — last three numeric fields after formula block.
    floats = _FLOAT_TOKEN.findall(body)
    if len(floats) < 3:
        raise NasaCeaConventionError(
            f"CEA header missing phase/MW/Hf fields: {line!r}"
        )
    # Formula tokens: element symbols alternating with counts before phase.
    # Phase is an integer 0..8 typically right before MW.
    # Walk tokens from the free string.
    tokens = re.findall(r"[A-Za-z]{1,2}|[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[DdEe][+-]?\d+)?", body)
    # Find last three pure-numeric as phase, MW, Hf — but phase is integer-like
    # and MW/Hf are floats. Safer: from the right, Hf = last, MW = second last,
    # phase = third last if it looks like a small integer.
    num_vals = [_parse_fortran_float(t) for t in floats]
    hf = num_vals[-1]
    mw = num_vals[-2]
    phase = int(round(num_vals[-3]))
    # Formula: everything in body before the phase/MW/Hf triple.
    # Rebuild from fixed columns when possible (CEA classic layout).
    # Classic: after ref/date, five (element,count) pairs in fixed columns,
    # then phase, MW, Hf.
    formula_tokens = _extract_formula_tokens(s)
    return {
        "n_intervals": nint,
        "source_ref_code": ref,
        "formula_tokens": formula_tokens,
        "phase_flag": phase,
        "molecular_weight_g_per_mol": mw,
        "delta_f_H_298_15_J_per_mol": hf,
    }


def _extract_formula_tokens(header_line: str) -> list[str]:
    """Extract up to five (element, count) pairs from a CEA header line.

    Classic column layout (1-indexed, CAP / thermo.inp):
      1-2 nint, 3-10 ref+date (variable), then element blocks, then
      phase at col 51, MW 53-65, Hf 66-80 (approximate; we free-parse).
    """
    s = header_line.rstrip()
    # Drop leading nint
    body = s[2:]
    # Remove ref code at start (glued g10/97, spaced g 8/89, word+year, or bare).
    body = re.sub(
        r"^\s*(?:"
        r"[A-Za-z]+\d+/\d{2}"
        r"|[A-Za-z]+\s+\d{1,2}/\d{2}"
        r"|[A-Za-z]+\s+\d{2}"
        r"|[A-Za-z]+[0-9]*"
        r")\s*",
        "",
        body,
        count=1,
    )
    # Now body starts with element symbols. Scan element+count pairs until
    # we hit the phase integer followed by MW and Hf.
    tokens = re.findall(
        r"[A-Za-z]{1,2}|[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[DdEe][+-]?\d+)?",
        body,
    )
    # Drop trailing phase, MW, Hf (three numeric tokens). Charge markers like
    # E -1.00 are part of the formula block.
    if len(tokens) < 3:
        return tokens
    # Identify trailing triple: phase (small int), MW (>0), Hf (any).
    # Walk from end.
    formula = tokens[:-3]
    # Filter zero-count fillers (0.00 after empty element slots of "0.00")
    cleaned: list[str] = []
    i = 0
    while i < len(formula):
        tok = formula[i]
        if re.fullmatch(r"[A-Za-z]{1,2}", tok):
            if i + 1 < len(formula):
                cleaned.extend([tok, formula[i + 1]])
                i += 2
                continue
        # Skip lone zeros from empty element slots (numeric without element)
        try:
            if abs(_parse_fortran_float(tok)) < 1e-15:
                i += 1
                continue
        except ValueError:
            pass
        cleaned.append(tok)
        i += 1
    return cleaned


@dataclass
class BulkSkipReport:
    """Provenance for classified bulk skips (inverted/zero-width only).

    Non-classifiable parse defects always raise; they never enter this report.
    """

    dropped_inverted_segments: list[dict[str, Any]] = field(default_factory=list)
    skipped_species: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dropped_inverted_segments": list(self.dropped_inverted_segments),
            "skipped_species": list(self.skipped_species),
        }


def parse_thermo_inp(
    text: str,
    *,
    skip_invalid_segments: bool = False,
    skip_report: BulkSkipReport | None = None,
) -> list[CeaSpeciesRecord]:
    """Parse a NASA CEA ``thermo.inp`` (or subset) into preserved records.

    Parameters
    ----------
    skip_invalid_segments:
        When True (bulk / full-database mode), drop **only** intervals with
        ``T_min >= T_max`` (Snyder 2021 T-range floor artifact in the public
        ``thermo.inp``) with a stderr warning, and skip species that retain no
        valid intervals after those classified drops. All other parse /
        segment defects (gaps, truncated blocks, bad coefficients, convention
        errors) always raise — bulk mode is fail-closed for unrecognized
        defects. Default False keeps fail-loud behaviour for fixtures and
        unit tests (inverted segments raise too).
    skip_report:
        Optional mutable report that receives dropped-segment and
        skipped-species provenance under bulk mode.
    """
    report = skip_report if skip_report is not None else BulkSkipReport()
    lines = text.splitlines()
    # Skip to after the ``thermo`` marker when present.
    i = 0
    while i < len(lines):
        if lines[i].strip().lower() == "thermo":
            i += 1
            # optional date line
            if i < len(lines) and not _is_species_name_line(lines[i]):
                i += 1
            break
        i += 1
    else:
        i = 0

    records: list[CeaSpeciesRecord] = []
    while i < len(lines):
        line = lines[i]
        if line.strip().upper().startswith("END"):
            break
        if line.startswith("!") or not line.strip():
            i += 1
            continue
        if not _is_species_name_line(line):
            i += 1
            continue
        name = line[0:18].rstrip() if len(line) >= 18 else line.strip()
        citation = line[18:].strip() if len(line) > 18 else ""
        if i + 1 >= len(lines):
            raise NasaCeaConventionError(f"{name}: truncated record (no header)")
        header = _parse_header_line(lines[i + 1])
        nint = int(header["n_intervals"])
        intervals: list[dict[str, Any]] = []
        dropped_inverted = 0
        cursor = i + 2
        for _ in range(nint):
            if cursor + 2 >= len(lines):
                raise NasaCeaConventionError(
                    f"{name}: truncated interval block (need 3 lines)"
                )
            t_line = lines[cursor]
            c1 = lines[cursor + 1]
            c2 = lines[cursor + 2]
            # T-line free-token parse. NASA CAP commonly glues n_poly onto
            # T_max (e.g. "1000.0007" ⇒ T_max=1000.000, n_poly=7) when the
            # high bound is printed with three decimals.
            raw_tokens = _FLOAT_TOKEN.findall(t_line)
            if len(raw_tokens) < 2:
                raise NasaCeaConventionError(
                    f"{name}: interval T-line missing bounds: {t_line!r}"
                )
            T_min = _parse_fortran_float(raw_tokens[0])
            t_max_token = raw_tokens[1]
            n_poly = 7
            glued = re.fullmatch(
                r"([+-]?\d+\.\d{3})([5-9])", t_max_token.replace(" ", "")
            )
            if glued:
                T_max = float(glued.group(1))
                n_poly = int(glued.group(2))
                after = raw_tokens[2:]
            else:
                # Try integer n_poly as its own token after T_max
                T_max = _parse_fortran_float(t_max_token)
                after = raw_tokens[2:]
                if after and re.fullmatch(r"[5-9]", after[0]):
                    n_poly = int(after[0])
                    after = after[1:]

            exponents: tuple[float, ...] = NASA9_DEFAULT_EXPONENTS
            if len(after) >= 7:
                exponents = tuple(_parse_fortran_float(x) for x in after[:8])

            c1_vals = _parse_coeff_line_16(c1)
            c2_vals = _parse_coeff_line_16(c2)
            # 5 coeffs on line 1, 2 + b1 + b2 on line 2 (with possible blanks)
            if len(c1_vals) < 5 or len(c2_vals) < 4:
                # Some records pack differently; require 7 a's + 2 b's total.
                all_c = c1_vals + c2_vals
                if len(all_c) < 9:
                    raise NasaCeaConventionError(
                        f"{name}: expected 9 NASA-9 coefficients (7a+2b); "
                        f"got {len(all_c)} from lines {c1!r} / {c2!r}"
                    )
                a_coeffs = all_c[:7]
                b1, b2 = all_c[7], all_c[8]
            else:
                a_coeffs = c1_vals[:5] + c2_vals[:2]
                b1, b2 = c2_vals[-2], c2_vals[-1]

            if n_poly != 7:
                raise NasaCeaConventionError(
                    f"{name}: only 7-term NASA-9 polynomials supported; "
                    f"got n_poly={n_poly}"
                )

            if not (float(T_min) < float(T_max)):
                # Classified Snyder-2021 floor artifact only. Every other
                # defect refuses — bulk mode must not swallow gaps / convention
                # / coefficient errors as "invalid segments".
                msg = (
                    f"{name}: inverted/zero-width segment "
                    f"T=[{T_min}, {T_max}] K"
                )
                if skip_invalid_segments:
                    print(f"warning: dropping {msg}", file=sys.stderr)
                    dropped_inverted += 1
                    report.dropped_inverted_segments.append(
                        {
                            "cea_name": name,
                            "T_min_K": float(T_min),
                            "T_max_K": float(T_max),
                            "reason": "inverted_or_zero_width_T_range",
                        }
                    )
                    cursor += 3
                    continue
                raise NasaCeaSegmentError(
                    f"NASA-9 segment requires T_min < T_max; got "
                    f"[{float(T_min)}, {float(T_max)}]"
                )

            intervals.append(
                {
                    "T_min_K": float(T_min),
                    "T_max_K": float(T_max),
                    "n_poly_terms": 7,
                    "exponents": list(exponents[:8])
                    if len(exponents) >= 7
                    else list(NASA9_DEFAULT_EXPONENTS),
                    "a_coefficients": [float(x) for x in a_coeffs],
                    "b1": float(b1),
                    "b2": float(b2),
                }
            )
            cursor += 3

        if not intervals:
            if skip_invalid_segments and dropped_inverted > 0:
                # Only skip the whole species when every interval was the
                # classified inverted/zero-width drop — not for other defects.
                print(
                    f"warning: skipping {name}: no valid T segments after "
                    "dropping inverted/zero-width intervals",
                    file=sys.stderr,
                )
                report.skipped_species.append(
                    {
                        "cea_name": name,
                        "reason": "no_valid_segments_after_inverted_drops",
                        "source_n_intervals": nint,
                        "dropped_inverted_segments": dropped_inverted,
                    }
                )
                i = cursor
                continue
            raise NasaCeaSegmentError(
                f"{name}: no valid temperature segments after parse"
            )

        std = _phase_flag_to_standard_state(int(header["phase_flag"]), name)
        records.append(
            CeaSpeciesRecord(
                name=name,
                citation=citation,
                n_intervals=len(intervals),
                source_ref_code=str(header["source_ref_code"]),
                formula_tokens=list(header["formula_tokens"]),
                phase_flag=int(header["phase_flag"]),
                standard_state=std,
                molecular_weight_g_per_mol=float(
                    header["molecular_weight_g_per_mol"]
                ),
                delta_f_H_298_15_J_per_mol=float(
                    header["delta_f_H_298_15_J_per_mol"]
                ),
                intervals=intervals,
                raw_header_line=lines[i + 1],
                raw_name_line=line,
                source_n_intervals=nint,
                dropped_inverted_segments=dropped_inverted,
            )
        )
        # Construction validates segment coverage via to_polynomial.
        # Fail-closed: post-parse defects (gaps, domain, convention) ALWAYS
        # raise, even under --skip-invalid-segments. Bulk mode may only drop
        # the classified inverted/zero-width T-range class above.
        records[-1].to_polynomial()
        i = cursor

    return records


# ---------------------------------------------------------------------------
# REV5 four-strata DRAFT emission
# ---------------------------------------------------------------------------

# Map volatile-draft species keys → preferred CEA name pairs (gas, condensed…)
_VOLATILE_CEA_TARGETS: dict[str, list[str]] = {
    "H2O_ice": ["H2O", "H2O(cr)", "H2O(L)"],
    "CO2_frost": ["CO2"],
    "CO": ["CO"],
    "CH4": ["CH4"],
    "NH3": ["NH3"],
    "O2": ["O2"],
}


@dataclass
class IngestResult:
    records: list[CeaSpeciesRecord] = field(default_factory=list)
    draft_document: dict[str, Any] = field(default_factory=dict)
    bulk_skip_report: BulkSkipReport | None = None


# NASA/TP-2002-211556 mixed standard-state pressures (report p. 2):
# ideal gases at 1 bar; pure condensed reference substances at 1 atm.
_CEA_GAS_REF_PRESSURE_PA = 100_000.0
_CEA_CONDENSED_REF_PRESSURE_PA = 101_325.0
_CEA_GAS_REF_CONVENTION = "CEA_JANAF_1_bar"
_CEA_CONDENSED_REF_CONVENTION = "CEA_condensed_1_atm"


def _canonical_gas_id(cea_name: str) -> str:
    """Collision-light ID for draft rows (condensed suffixes preserved)."""
    return cea_name.replace("(", "_").replace(")", "").replace(",", "_")


def _family_id_for(record: CeaSpeciesRecord) -> str:
    base = record.name.split("(")[0]
    # Strip charge markers for family grouping.
    base = base.rstrip("+-")
    return f"cea_{base}"


def _base_chemical_name(cea_name: str) -> str:
    """Strip phase/allotrope suffix: 'C(gr)' → 'C', 'Fe2O3(cr)' → 'Fe2O3'."""
    return cea_name.split("(", 1)[0].rstrip("+-")


def _is_gas_record(record: CeaSpeciesRecord) -> bool:
    return int(record.phase_flag) == 0 or record.standard_state == "gas"


def _ref_pressure_fields(record: CeaSpeciesRecord) -> tuple[float, str]:
    """Return (reference_pressure_Pa, convention) for a CEA record.

    Gas: ideal-gas standard state at 1 bar. Condensed: pure crystalline/liquid
    reference substance at 1 atm (NASA/TP-2002-211556 p. 2). Never stamp
    condensed rows as 1 bar.
    """
    if _is_gas_record(record):
        return _CEA_GAS_REF_PRESSURE_PA, _CEA_GAS_REF_CONVENTION
    return _CEA_CONDENSED_REF_PRESSURE_PA, _CEA_CONDENSED_REF_CONVENTION


def _merge_same_name_records(
    records: Sequence[CeaSpeciesRecord],
) -> list[CeaSpeciesRecord]:
    """Collapse duplicate CEA names into one record without losing intervals.

    ``thermo.inp`` can emit multiple same-name condensed branches (e.g. two
    ``Fe2O3(cr)`` Curie-window records). Family/species keying must not silently
    overwrite the first; merge intervals in T order and preserve provenance.
    """
    groups: dict[str, list[CeaSpeciesRecord]] = {}
    order: list[str] = []
    for rec in records:
        if rec.name not in groups:
            order.append(rec.name)
            groups[rec.name] = []
        groups[rec.name].append(rec)

    merged: list[CeaSpeciesRecord] = []
    for name in order:
        group = groups[name]
        if len(group) == 1:
            merged.append(group[0])
            continue

        # Stable T-order merge; adjacency is allowed, overlap fails loud.
        ordered = sorted(
            group,
            key=lambda r: (
                r.intervals[0]["T_min_K"],
                r.intervals[-1]["T_max_K"],
            ),
        )
        intervals: list[dict[str, Any]] = []
        citations: list[str] = []
        refs: list[str] = []
        phase_flags: list[int] = []
        source_n_intervals = 0
        dropped_inverted = 0
        for rec in ordered:
            if rec.citation and rec.citation not in citations:
                citations.append(rec.citation)
            if rec.source_ref_code and rec.source_ref_code not in refs:
                refs.append(rec.source_ref_code)
            phase_flags.append(int(rec.phase_flag))
            source_n_intervals += int(
                rec.source_n_intervals or rec.n_intervals
            )
            dropped_inverted += int(rec.dropped_inverted_segments)
            for iv in rec.intervals:
                if intervals:
                    prev_max = float(intervals[-1]["T_max_K"])
                    cur_min = float(iv["T_min_K"])
                    if cur_min < prev_max - 1e-9:
                        raise NasaCeaSegmentError(
                            f"{name}: cannot merge same-name CEA records — "
                            f"overlapping intervals ending at {prev_max} K and "
                            f"starting at {cur_min} K"
                        )
                intervals.append(dict(iv))

        head = ordered[0]
        # Prefer solid/liquid tag from any member; phase_flag keeps first-branch
        # identity and the full list is recorded in citation/provenance notes.
        std = head.standard_state
        for rec in ordered[1:]:
            if rec.standard_state != "gas" and std == "gas":
                std = rec.standard_state
        citation = " | ".join(citations) if citations else head.citation
        if len(set(phase_flags)) > 1:
            citation = (
                f"{citation} [merged {len(ordered)} same-name records; "
                f"phase_flags={phase_flags}]"
            ).strip()
        ref_code = refs[0] if len(refs) == 1 else " | ".join(refs)

        merged_rec = CeaSpeciesRecord(
            name=name,
            citation=citation,
            n_intervals=len(intervals),
            source_ref_code=ref_code,
            formula_tokens=list(head.formula_tokens),
            phase_flag=int(head.phase_flag),
            standard_state=std,
            molecular_weight_g_per_mol=float(head.molecular_weight_g_per_mol),
            delta_f_H_298_15_J_per_mol=float(head.delta_f_H_298_15_J_per_mol),
            intervals=intervals,
            raw_header_line=head.raw_header_line,
            raw_name_line=head.raw_name_line,
            source_n_intervals=source_n_intervals,
            dropped_inverted_segments=dropped_inverted,
        )
        # Validate merged segment chain (gaps/overlap) via polynomial builder.
        merged_rec.to_polynomial()
        merged.append(merged_rec)
    return merged


def _record_to_thermo_payload(record: CeaSpeciesRecord) -> dict[str, Any]:
    """Serialize a preserved CEA record (coefficients intact — no refit)."""
    ref_pa, ref_conv = _ref_pressure_fields(record)
    payload: dict[str, Any] = {
        "cea_name": record.name,
        "evaluator_family": "nasa_cea_9",
        "standard_state": record.standard_state,
        "phase_flag": record.phase_flag,
        "formula": _formula_from_tokens(record.formula_tokens),
        "formula_tokens": list(record.formula_tokens),
        "molecular_weight_g_per_mol": record.molecular_weight_g_per_mol,
        "delta_f_H_298_15_J_per_mol": record.delta_f_H_298_15_J_per_mol,
        "source_ref_code": record.source_ref_code,
        "citation": record.citation,
        "reference_pressure_Pa": ref_pa,
        "reference_pressure_convention": ref_conv,
        "n_intervals": record.n_intervals,
        "source_n_intervals": int(
            record.source_n_intervals or record.n_intervals
        ),
        "segments": [
            {
                "T_min_K": iv["T_min_K"],
                "T_max_K": iv["T_max_K"],
                "exponents": iv["exponents"],
                "a_coefficients": iv["a_coefficients"],
                "b1": iv["b1"],
                "b2": iv["b2"],
            }
            for iv in record.intervals
        ],
        "domain_K": [
            record.intervals[0]["T_min_K"],
            record.intervals[-1]["T_max_K"],
        ],
    }
    if record.dropped_inverted_segments:
        payload["dropped_inverted_segments"] = int(
            record.dropped_inverted_segments
        )
    return payload


def _paired_gas_name(
    condensed: CeaSpeciesRecord,
    gas_names: Mapping[str, CeaSpeciesRecord],
) -> str | None:
    """Return a same-formula gas CEA name already in the emission set, if any.

    Condensed Gibbs alone cannot define P_sat. Only claim
    ``pure_component_psat_from_delta_g`` when the matching gas record is also
    emitted (atom-balanced gas-minus-condensed pair).
    """
    base = _base_chemical_name(condensed.name)
    gas = gas_names.get(base)
    if gas is None:
        return None
    cond_formula = _formula_from_tokens(condensed.formula_tokens)
    gas_formula = _formula_from_tokens(gas.formula_tokens)
    if cond_formula != gas_formula:
        return None
    return gas.name


def build_four_strata_draft(
    records: Sequence[CeaSpeciesRecord],
    *,
    source_thermo_path: str | None = None,
    trial_volatile_path: str | None = None,
    species_filter: set[str] | None = None,
) -> dict[str, Any]:
    """Emit a REV5 four-strata DRAFT document (never production-enabled)."""
    selected_raw = [
        r
        for r in records
        if species_filter is None or r.name in species_filter
    ]
    # Merge same-name multi-branch records (Fe2O3(cr) Curie pair, etc.) so
    # species-key emission cannot silently drop intervals.
    selected = _merge_same_name_records(selected_raw)
    gas_by_name = {r.name: r for r in selected if _is_gas_record(r)}

    families: dict[str, Any] = {}
    emitted_species = 0
    for rec in selected:
        # Loud: every CEA draft row must carry validation.status.
        validation_status = "pending_validation"
        fam = _family_id_for(rec)
        gas_id = _canonical_gas_id(rec.name)
        fam_block = families.setdefault(
            fam,
            {
                "physical_properties": {"species": {}},
                "fiat_routing": {
                    "plant_bin": None,
                    "engineering_capture_policy": None,
                    "products_and_coproducts": [],
                    "process_or_terminal_destination": None,
                    "note": "DRAFT: fiat routing deferred to owner/physics review",
                },
                "vaporisation_coefficients": {
                    "evaporation_alpha": None,
                    "alpha_domain_and_uncertainty": None,
                    "extrapolation_policy": "conservative_slope_continuation",
                    "out_of_range_status": "out_of_range_conservative_continuation",
                    "acquisition_flag": "cea_ingest_draft",
                    "note": "DRAFT: alpha not assigned by CEA thermo ingest",
                },
                "code_metadata": {
                    "formula_id": None,
                    "source_account": None,
                    "request_rule": None,
                    "solve_group_id": fam,
                    "compatibility_projection": None,
                    "canonical_aliases": [rec.name, gas_id],
                    "cea_source_name": rec.name,
                },
            },
        )
        if gas_id in fam_block["physical_properties"]["species"]:
            raise NasaCeaConventionError(
                f"duplicate draft species identity {fam}/{gas_id} for CEA "
                f"record {rec.name!r} — merge failed to uniquify emission keys"
            )

        source_reactions: list[dict[str, Any]] = []
        if _is_gas_record(rec):
            pressure_kind = "gas_standard_state_thermo"
        else:
            paired = _paired_gas_name(rec, gas_by_name)
            if paired is not None:
                pressure_kind = "pure_component_psat_from_delta_g"
                source_reactions.append(
                    {
                        "kind": "gas_minus_condensed_delta_g",
                        "reaction": f"{rec.name} = {paired}",
                        "condensed_cea_name": rec.name,
                        "gas_cea_name": paired,
                        "gas_standard_pressure_Pa": _CEA_GAS_REF_PRESSURE_PA,
                        "condensed_standard_pressure_Pa": (
                            _CEA_CONDENSED_REF_PRESSURE_PA
                        ),
                        "note": (
                            "Executable pure-component P_sat/P° only via "
                            "gas-minus-condensed ΔG with both records present; "
                            "gas P° = 1 bar, condensed reference = 1 atm "
                            "(NASA/TP-2002-211556)."
                        ),
                    }
                )
            else:
                # Condensed G° alone is thermochemistry evidence, not P_sat.
                pressure_kind = "condensed_standard_state_thermo"

        pressure_model: dict[str, Any] = {
            "evaluator_family": "nasa_cea_9",
            "pressure_kind": pressure_kind,
            "species_basis": "monomer",
            "valid_domain": {
                "T_min_K": rec.intervals[0]["T_min_K"],
                "T_max_K": rec.intervals[-1]["T_max_K"],
            },
            "standard_state": rec.standard_state,
            "thermo_record": _record_to_thermo_payload(rec),
            "provenance": [
                {
                    "source": "NASA_CEA_thermo.inp",
                    "ref_code": rec.source_ref_code,
                    "citation": rec.citation,
                    "locator": source_thermo_path,
                    "note": (
                        "Source coefficients preserved; runtime evaluates "
                        "polynomials directly — no spreadsheet refit."
                    ),
                }
            ],
        }
        # Missing convention would have failed at parse/to_polynomial; re-check.
        if not rec.standard_state:
            raise NasaCeaConventionError(
                f"{rec.name}: missing standard_state convention"
            )
        species_row = {
            "formula": _formula_from_tokens(rec.formula_tokens),
            "source_reactions": source_reactions,
            "pressure_models": [pressure_model],
            "phase_properties": [
                {
                    "phase": rec.standard_state,
                    "cea_phase_flag": rec.phase_flag,
                    "molecular_weight_g_per_mol": rec.molecular_weight_g_per_mol,
                    "delta_f_H_298_15_J_per_mol": rec.delta_f_H_298_15_J_per_mol,
                }
            ],
            "validation": {
                "status": validation_status,
                "anchor_refs": [],
                "note": (
                    "DRAFT pending_validation — not production authority; "
                    "owner + physics review required before enablement."
                ),
            },
        }
        if pressure_kind == "condensed_standard_state_thermo":
            species_row["validation"]["note"] = (
                "DRAFT pending_validation — condensed standard-state "
                "thermochemistry only; no same-formula gas/reaction pair in "
                "this emission set, so pure_component_psat_from_delta_g is "
                "not claimed. Owner + physics review required before enablement."
            )
        fam_block["physical_properties"]["species"][gas_id] = species_row
        emitted_species += 1
        # Ensure code_metadata aliases accumulate when multiple phases share family.
        aliases = fam_block["code_metadata"]["canonical_aliases"]
        for a in (rec.name, gas_id):
            if a not in aliases:
                aliases.append(a)

    doc: dict[str, Any] = {
        "schema_version": 2,
        "kind": "vapour_rail_cea_draft",
        "status": "literature_draft_not_runtime_authority",
        "enabled_for_merge": False,
        "enabled_for_production_yaml": False,
        "conventions": {
            "evaluator_families": ["nasa_cea_7", "nasa_cea_9"],
            "reference_pressure_Pa_gas": _CEA_GAS_REF_PRESSURE_PA,
            "reference_pressure_Pa_condensed": _CEA_CONDENSED_REF_PRESSURE_PA,
            # Gas default retained for consumers that only read the legacy key;
            # per-record thermo_record.reference_pressure_* is authoritative.
            "reference_pressure_Pa": _CEA_GAS_REF_PRESSURE_PA,
            "reference_pressure_note": (
                "NASA/TP-2002-211556 mixed standard states: ideal gases at "
                "1 bar (1e5 Pa); pure condensed (cr/L) reference substances "
                "at 1 atm (101325 Pa). Per-record "
                "thermo_record.reference_pressure_convention is authoritative; "
                "do not rewrite condensed rows as 1 bar."
            ),
            "runtime_policy": (
                "Evaluate preserved source polynomials over declared segments. "
                "Never refit spreadsheet rows at runtime. Segment gap/overlap "
                "and missing standard-state convention fail loudly. "
                "pure_component_psat_from_delta_g requires an explicit "
                "gas-minus-condensed source_reactions bundle; unpaired "
                "condensed rows are condensed_standard_state_thermo only."
            ),
            "citations": [
                "McBride, Zehe, Gordon, NASA TP-2002-211556",
                "NASA CEA thermo.inp (Glenn coefficients)",
            ],
        },
        "provenance": {
            "source_thermo": source_thermo_path,
            "trial_volatile_draft": trial_volatile_path,
            "ingest_tool": "tools/vp_cea_ingest.py",
            "chunk": "VR-4",
        },
        "families": families,
        "record_count": emitted_species,
        "validation_gate": {
            "default_status": "pending_validation",
            "production_yaml_enabled": False,
        },
    }
    # Loud failure if any emitted species lacks validation.status
    counted = 0
    for fam_id, fam in families.items():
        for sp_id, sp in fam["physical_properties"]["species"].items():
            counted += 1
            status = (sp.get("validation") or {}).get("status")
            if not status:
                raise NasaCeaConventionError(
                    f"CEA draft row {fam_id}/{sp_id} missing validation.status"
                )
            std = sp["pressure_models"][0].get("standard_state")
            if not std:
                raise NasaCeaConventionError(
                    f"CEA draft row {fam_id}/{sp_id} missing standard_state"
                )
            pm = sp["pressure_models"][0]
            tr = pm.get("thermo_record") or {}
            if not tr.get("reference_pressure_convention"):
                raise NasaCeaConventionError(
                    f"CEA draft row {fam_id}/{sp_id} missing "
                    "reference_pressure_convention"
                )
            if (
                pm.get("pressure_kind") == "pure_component_psat_from_delta_g"
                and not sp.get("source_reactions")
            ):
                raise NasaCeaConventionError(
                    f"CEA draft row {fam_id}/{sp_id} claims "
                    "pure_component_psat_from_delta_g without source_reactions"
                )
    if counted != emitted_species:
        raise NasaCeaConventionError(
            f"record_count mismatch: emitted={emitted_species} "
            f"walked={counted}"
        )
    return doc


def load_volatile_draft_species(path: Path) -> list[str]:
    """Return volatile-draft row keys for trial CEA targeting."""
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, Mapping):
        raise NasaCeaConventionError(f"{path}: expected mapping root")
    block = data.get("volatile_species_DRAFT_FOR_REVIEW") or data
    rows = block.get("rows") if isinstance(block, Mapping) else None
    if not isinstance(rows, Mapping):
        raise NasaCeaConventionError(
            f"{path}: expected volatile_species_DRAFT_FOR_REVIEW.rows mapping"
        )
    return list(rows.keys())


def resolve_trial_cea_names(
    volatile_keys: Iterable[str],
    available: Mapping[str, CeaSpeciesRecord],
) -> set[str]:
    """Map volatile draft keys to CEA names present in the thermo parse."""
    wanted: set[str] = set()
    for key in volatile_keys:
        targets = _VOLATILE_CEA_TARGETS.get(key, [key, key.split("_")[0]])
        for t in targets:
            if t in available:
                wanted.add(t)
    return wanted


class CeaIngestSelectionError(NasaCeaError):
    """Explicit --species request unmatched or empty selection (fail-closed)."""


class CeaSpeciesFileError(NasaCeaError):
    """--species-file missing, empty, or malformed (fail-closed bulk gate)."""


def load_species_file(path: Path) -> list[str]:
    """Load one CEA species name per line from a bulk selection file.

    Fail-closed:
    - path must be an existing regular file
    - after stripping blank lines and ``#`` comments, at least one name must
      remain (an empty bulk selection must not silently widen to the full
      thermo parse)
    - non-empty lines must be a single CEA name token (no interior whitespace);
      multi-token / garbage lines refuse rather than partial-parse
    """
    if not path.is_file():
        raise CeaSpeciesFileError(f"--species-file not found: {path}")
    try:
        raw_text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise CeaSpeciesFileError(
            f"--species-file is not valid UTF-8: {path}: {exc}"
        ) from exc

    names: list[str] = []
    for lineno, raw in enumerate(raw_text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # CEA species names are single tokens (e.g. O2, H2O(cr), Fe2O3(cr)).
        # Interior whitespace means the line is malformed for this bulk format.
        if any(ch.isspace() for ch in line):
            raise CeaSpeciesFileError(
                f"--species-file {path}:{lineno}: expected one CEA species "
                f"name per line, got multi-token {line!r}"
            )
        names.append(line)
    if not names:
        raise CeaSpeciesFileError(
            f"--species-file empty after comments/blanks: {path}"
        )
    return names


def ingest(
    thermo_path: Path,
    *,
    volatile_draft: Path | None = None,
    species: Sequence[str] | None = None,
    skip_invalid_segments: bool = False,
) -> IngestResult:
    text = thermo_path.read_text(encoding="utf-8", errors="replace")
    skip_report = BulkSkipReport() if skip_invalid_segments else None
    records = parse_thermo_inp(
        text,
        skip_invalid_segments=skip_invalid_segments,
        skip_report=skip_report,
    )
    by_name = {r.name: r for r in records}
    species_filter: set[str] | None = None
    # Fail-closed: an explicit empty selection (species=[]) must refuse, not
    # silently widen to the full parse. Only species is None means "no filter".
    if species is not None:
        requested = list(species)
        if not requested:
            raise CeaIngestSelectionError(
                "explicit --species selection is empty"
            )
        missing = sorted({name for name in requested if name not in by_name})
        if missing:
            raise CeaIngestSelectionError(
                "explicit --species not present in thermo parse: "
                + ", ".join(missing)
            )
        species_filter = set(requested)
        if not species_filter:
            raise CeaIngestSelectionError(
                "explicit --species selection is empty after validation"
            )
    if volatile_draft is not None:
        keys = load_volatile_draft_species(volatile_draft)
        trial = resolve_trial_cea_names(keys, by_name)
        if species_filter is None:
            species_filter = trial
        else:
            species_filter |= trial
    if species_filter is not None and not species_filter:
        raise CeaIngestSelectionError(
            "CEA ingest selection is empty (no matching species after filters)"
        )
    draft = build_four_strata_draft(
        records,
        source_thermo_path=_portable_path_str(thermo_path),
        trial_volatile_path=(
            _portable_path_str(volatile_draft) if volatile_draft else None
        ),
        species_filter=species_filter,
    )
    if skip_report is not None and (
        skip_report.dropped_inverted_segments or skip_report.skipped_species
    ):
        draft["bulk_skip_report"] = skip_report.as_dict()
    return IngestResult(
        records=records,
        draft_document=draft,
        bulk_skip_report=skip_report,
    )


def _portable_path_str(path: Path) -> str:
    """Prefer repo-relative provenance paths over absolute author-machine paths."""

    resolved = path.expanduser()
    try:
        return str(resolved.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def _yaml_dump(doc: dict[str, Any]) -> str:
    return yaml.safe_dump(
        doc,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=100,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--thermo",
        type=Path,
        required=True,
        help="path to NASA CEA thermo.inp (or fixture subset)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="DRAFT four-strata YAML output (never production-enabled)",
    )
    parser.add_argument(
        "--volatile-draft",
        type=Path,
        default=None,
        help=(
            "optional t-425 volatile draft YAML "
            "(docs-private/.../vp-volatile-rows-DRAFT.yaml) for trial targeting"
        ),
    )
    parser.add_argument(
        "--species",
        nargs="*",
        default=None,
        help="optional explicit CEA species names to include",
    )
    parser.add_argument(
        "--species-file",
        type=Path,
        default=None,
        help=(
            "bulk mode: path to a text file with one CEA species name per "
            "line (# comments and blank lines ignored); merged with --species"
        ),
    )
    parser.add_argument(
        "--skip-invalid-segments",
        action="store_true",
        help=(
            "bulk / full-database mode: drop ONLY inverted/zero-width T "
            "segments (Snyder 2021 thermo.inp floor artifacts) with warnings; "
            "all other parse/segment defects still fail closed"
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list parsed CEA names and exit (no write)",
    )
    args = parser.parse_args(argv)

    if not args.thermo.is_file():
        print(f"error: thermo file not found: {args.thermo}", file=sys.stderr)
        return 2

    # Fail-closed: distinguish "flag absent" (None → full parse) from
    # "flag present with zero names" ([] → refuse). Never coerce empty to None.
    species: list[str] | None = (
        list(args.species) if args.species is not None else None
    )
    if args.species_file is not None:
        try:
            from_file = load_species_file(args.species_file)
        except CeaSpeciesFileError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if species is None:
            species = from_file
        else:
            species = list(species) + from_file

    try:
        result = ingest(
            args.thermo,
            volatile_draft=args.volatile_draft,
            species=species,
            skip_invalid_segments=args.skip_invalid_segments,
        )
    except (
        CeaIngestSelectionError,
        CeaSpeciesFileError,
        NasaCeaSegmentError,
        NasaCeaConventionError,
        NasaCeaError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.list:
        for rec in result.records:
            print(
                f"{rec.name:20s} phase={rec.phase_flag} "
                f"state={rec.standard_state:18s} "
                f"nint={rec.n_intervals} ref={rec.source_ref_code}"
            )
        return 0

    if args.output is None:
        print("error: --output is required unless --list is set", file=sys.stderr)
        return 2

    # Hard gate: never claim production enablement.
    doc = result.draft_document
    if doc.get("enabled_for_merge") or doc.get("enabled_for_production_yaml"):
        print("error: internal gate — draft must not be production-enabled", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# DO NOT MERGE WITHOUT OWNER + PHYSICS REVIEW.\n"
        "# CEA ingest DRAFT only. enabled_for_merge: false.\n"
        "# Source coefficients preserved; runtime evaluates polynomials "
        "directly — no spreadsheet refit.\n"
        "# Generated by tools/vp_cea_ingest.py (VR-4 / t-425).\n"
    )
    args.output.write_text(header + _yaml_dump(doc), encoding="utf-8")
    n = doc.get("record_count", 0)
    print(f"wrote DRAFT four-strata rows for {n} CEA records → {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
