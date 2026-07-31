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
    # Reference code is the first non-date token: letters (+ optional digits)
    # Examples: "g 8/89", "tpis89", "j 3/78", "coda89"
    # Ref codes: "g", "j", "tpis89", "coda89", "srd…", optionally followed by
    # a month/year date like "8/89" for single-letter GRC/JANAF tags.
    m = re.match(
        r"(?P<ref>[A-Za-z]+[0-9]*)\s*(?P<date>\d{1,2}/\d{2})?\s*(?P<body>.*)$",
        rest,
    )
    if not m:
        raise NasaCeaConventionError(f"unparseable CEA header line: {line!r}")
    ref = m.group("ref")
    date = m.group("date")
    if date:
        ref = f"{ref} {date}"
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
    # Remove ref code + optional date at start
    body = re.sub(
        r"^\s*[A-Za-z]+\s*(?:\d{1,2}/\d{2,2}|\d{2,4})?\s*",
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


def parse_thermo_inp(text: str) -> list[CeaSpeciesRecord]:
    """Parse a NASA CEA ``thermo.inp`` (or subset) into preserved records."""
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

        std = _phase_flag_to_standard_state(int(header["phase_flag"]), name)
        records.append(
            CeaSpeciesRecord(
                name=name,
                citation=citation,
                n_intervals=nint,
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
            )
        )
        # Construction validates segment coverage via to_polynomial.
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


def _canonical_gas_id(cea_name: str) -> str:
    """Collision-light ID for draft rows (condensed suffixes preserved)."""
    return cea_name.replace("(", "_").replace(")", "").replace(",", "_")


def _family_id_for(record: CeaSpeciesRecord) -> str:
    base = record.name.split("(")[0]
    # Strip charge markers for family grouping.
    base = base.rstrip("+-")
    return f"cea_{base}"


def _record_to_thermo_payload(record: CeaSpeciesRecord) -> dict[str, Any]:
    """Serialize a preserved CEA record (coefficients intact — no refit)."""
    return {
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
        "reference_pressure_Pa": 100_000.0,
        "reference_pressure_convention": "CEA_JANAF_1_bar",
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


def build_four_strata_draft(
    records: Sequence[CeaSpeciesRecord],
    *,
    source_thermo_path: str | None = None,
    trial_volatile_path: str | None = None,
    species_filter: set[str] | None = None,
) -> dict[str, Any]:
    """Emit a REV5 four-strata DRAFT document (never production-enabled)."""
    selected = [
        r
        for r in records
        if species_filter is None or r.name in species_filter
    ]
    families: dict[str, Any] = {}
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
        pressure_model: dict[str, Any] = {
            "evaluator_family": "nasa_cea_9",
            "pressure_kind": (
                "pure_component_psat_from_delta_g"
                if rec.standard_state != "gas"
                else "gas_standard_state_thermo"
            ),
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
            "source_reactions": [],
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
        fam_block["physical_properties"]["species"][gas_id] = species_row
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
            "reference_pressure_Pa": 100_000.0,
            "reference_pressure_note": (
                "CEA/JANAF standard-state pressure P° = 1 bar = 1e5 Pa"
            ),
            "runtime_policy": (
                "Evaluate preserved source polynomials over declared segments. "
                "Never refit spreadsheet rows at runtime. Segment gap/overlap "
                "and missing standard-state convention fail loudly."
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
        "record_count": len(selected),
        "validation_gate": {
            "default_status": "pending_validation",
            "production_yaml_enabled": False,
        },
    }
    # Loud failure if any emitted species lacks validation.status
    for fam_id, fam in families.items():
        for sp_id, sp in fam["physical_properties"]["species"].items():
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
    """Explicit --species request unmatched or empty selection (CLI fail-loud)."""


def ingest(
    thermo_path: Path,
    *,
    volatile_draft: Path | None = None,
    species: Sequence[str] | None = None,
) -> IngestResult:
    text = thermo_path.read_text(encoding="utf-8", errors="replace")
    records = parse_thermo_inp(text)
    by_name = {r.name: r for r in records}
    species_filter: set[str] | None = None
    if species:
        requested = list(species)
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
    return IngestResult(records=records, draft_document=draft)


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
        "--list",
        action="store_true",
        help="list parsed CEA names and exit (no write)",
    )
    args = parser.parse_args(argv)

    if not args.thermo.is_file():
        print(f"error: thermo file not found: {args.thermo}", file=sys.stderr)
        return 2

    try:
        result = ingest(
            args.thermo,
            volatile_draft=args.volatile_draft,
            species=args.species,
        )
    except (
        CeaIngestSelectionError,
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
