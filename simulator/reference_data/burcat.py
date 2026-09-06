"""Burcat / Ruscic Third Millennium thermochemical compilation loader.

Parses ``BURCAT.THR.txt`` (NASA-7 4-line polynomial form with per-species
comment blocks) without unit conversion, smoothing, phase merging, or
silent repair. Reuses the NASA polynomial field parsers in
``simulator.reference_data.nasa_glenn``. The XML snapshot is a cross-check
only (it is the 2005 mirror; the THR file is current to 3 January 2023).

On-disk home: ``data/literature/compilations/burcat/``.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

import yaml

from simulator.reference_data.nasa_glenn import (
    COMPILATION_ROLE,
    ELEMENT_SYMBOLS,
    NASA7_COEFFICIENT_COUNT,
    PHASE_NORMALIZATION_RULE,
    CompositionSlot,
    NasaGlennParseError,
    PublishedNumber,
    coverage_by_element,
    feedstock_element_symbols,
    is_nasa7_four_line_record,
    parse_nasa7_coefficient_lines,
    parse_nasa7_header_line,
    published_float_pairs,
    resolve_phase,
)

ROOT = Path(__file__).resolve().parents[2]
COMPILATION_ROOT = ROOT / "data" / "literature" / "compilations" / "burcat"
SOURCE_REL = "data/literature/compilations/burcat/source/BURCAT.THR.txt"
XML_REL = "data/literature/compilations/burcat/source/BURCAT_THR.xml"
SIDECAR_REL = "data/literature/compilations/burcat/source/sidecar.yaml"
SCHEMA_VERSION = "literature_compilation.v1"
SOURCE_ID = "burcat"
MANIFEST_SCHEMA = "literature_compilation_manifest.v1"

OFFICIAL_URL = "https://respecth.elte.hu/burcat.php"
TECHNION_URL = "http://burcat.technion.ac.il/dir"
COMPILATION_SOURCE = {
    "database": (
        "Burcat and Ruscic, Third Millennium Ideal Gas and Condensed Phase "
        "Thermochemical Database for Combustion with updates from Active "
        "Thermochemical Tables"
    ),
    "citation": (
        "Burcat, A. and Ruscic, B., Third Millennium Ideal Gas and Condensed "
        "Phase Thermochemical Database for Combustion with updates from Active "
        "Thermochemical Tables, ANL-05/20 and TAE 960, Argonne National "
        "Laboratory / Technion, September 2005; current electronic files "
        "(species last added 3 January 2023) from the ELTE RESPECTH mirror "
        "of the Technion distribution. Database authors: Elke Goos, "
        "Alexander Burcat and Branko Ruscic."
    ),
    "version": "BURCAT.THR.txt, species last added 3 January 2023",
    "official_url": OFFICIAL_URL,
    "technion_url": TECHNION_URL,
    "licence": (
        "Quoted from BURCAT.THR.txt header: 'This database is provided free "
        "of charge for non commercial use, on condition that proper quotation "
        "will be included in the pertinent publications. IT IS STRICTLY "
        "FORBIDDEN TO INCLUDE THIS DATABASE AS IS OR PARTS OF IT IN ANY "
        "COMMERCIAL DATABASE, SOFTWARE, FIRMWARE OR HARDWARE AND ANY OTHER "
        "TYPE OF COMMERCIAL USE WITHOUT WRITTEN PERMISSION FROM THE AUTHORS.' "
        "Printed 2005 report is a US DOE technical report (ANL-05/20)."
    ),
}

_CAS_LINE_RE = re.compile(
    r"""^
    (?:
        N/A
      | \d{1,7}-\d{2}-\d(?:[A-Za-z?]+)?
      | \d{1,7}-\d{2}-\d{2}
      | \d{1,7}[.=\-]\d{2,4}[.=\-]\d{1,2}(?:[A-Za-z?]+)?
    )
    ,?
    (?:\s+.+)?
    $""",
    re.VERBOSE,
)
_SPECIES_START_MARK = "CHEMICAL ABSTRACT"


class BurcatParseError(NasaGlennParseError):
    """Unrecoverable BURCAT.THR parse defect."""


@dataclass
class Nasa7Interval:
    """One of the two published NASA-7 coefficient ranges (7 a-coefficients)."""

    range_as_published: str
    xml_range_tag: str
    a_coefficients: list[PublishedNumber]

    def to_dict(self) -> dict[str, Any]:
        return {
            "range_as_published": self.range_as_published,
            "xml_range_tag": self.xml_range_tag,
            "a_coefficients": [item.to_dict() for item in self.a_coefficients],
            "coefficient_count": sum(
                1 for item in self.a_coefficients if item.as_published.strip()
            ),
        }


@dataclass
class BurcatRecord:
    record_id: str
    name_as_published: str
    cas_as_published: str
    formula: str
    phase: str
    phase_ordinal: int | None
    phase_as_published: str
    phase_card_as_published: str
    date_as_published: str
    calc_quality_as_published: str
    composition_slots: list[CompositionSlot]
    molecular_weight: PublishedNumber
    T_min_K: PublishedNumber
    T_max_K: PublishedNumber
    intervals: list[Nasa7Interval]
    hf298_div_r: PublishedNumber | None
    comment_block: list[str]
    raw_header_line: str
    raw_coeff_lines: list[str]
    source_path: str
    cas_line_number: int | None
    header_line_number: int | None
    end_line_number: int
    record_kind: str
    ambiguities: list[dict[str, Any]] = field(default_factory=list)

    @property
    def coefficient_count(self) -> int:
        if self.record_kind != "nasa7_polynomial":
            return 0
        n = 0
        for interval in self.intervals:
            n += sum(1 for item in interval.a_coefficients if item.as_published.strip())
        if self.hf298_div_r is not None and self.hf298_div_r.as_published.strip():
            n += 1
        return n

    def elements(self) -> tuple[str, ...]:
        seen: list[str] = []
        for slot in self.composition_slots:
            if slot.filler or not slot.element:
                continue
            if slot.element not in seen:
                seen.append(slot.element)
        return tuple(seen)

    def to_dict(self) -> dict[str, Any]:
        hf = None
        if self.hf298_div_r is not None:
            hf = {
                **self.hf298_div_r.to_dict(),
                "units_as_published": "H298/R (dimensionless, as printed)",
            }
        payload = {
            "schema_version": SCHEMA_VERSION,
            "source_id": SOURCE_ID,
            "source": dict(COMPILATION_SOURCE),
            "compilation_role": dict(COMPILATION_ROLE),
            "record_id": self.record_id,
            "record_kind": self.record_kind,
            "name_as_published": self.name_as_published,
            "cas_as_published": self.cas_as_published,
            "formula": self.formula,
            "phase": self.phase,
            "phase_as_published": self.phase_as_published,
            "phase_card_as_published": self.phase_card_as_published,
            "phase_normalization_rule": PHASE_NORMALIZATION_RULE,
            "date_as_published": self.date_as_published,
            "calc_quality_as_published": self.calc_quality_as_published,
            "composition": [slot.to_dict() for slot in self.composition_slots],
            "molecular_weight": self.molecular_weight.to_dict(),
            "T_min_K": self.T_min_K.to_dict(),
            "T_max_K": self.T_max_K.to_dict(),
            "intervals": [interval.to_dict() for interval in self.intervals],
            "hf298_div_r": hf,
            "coefficient_count": self.coefficient_count,
            "interval_count": len(self.intervals),
            "comment_block": list(self.comment_block),
            "ambiguity_count": len(self.ambiguities),
            "ambiguities": list(self.ambiguities),
            "source_locator": {
                "path": self.source_path,
                "cas_line": self.cas_line_number,
                "header_line": self.header_line_number,
                "end_line": self.end_line_number,
            },
            "source_text": {
                "header_line": self.raw_header_line,
                "coeff_lines": list(self.raw_coeff_lines),
            },
        }
        if self.phase_ordinal is not None:
            payload["phase_ordinal"] = self.phase_ordinal
        return payload


@dataclass
class BurcatFile:
    records: list[BurcatRecord]
    source_path: str
    source_sha256: str
    xml_crosscheck: dict[str, Any]
    header_line_count: int
    footer_line_count: int


def _is_cas_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 120:
        return False
    return bool(_CAS_LINE_RE.match(stripped))


def _species_section_start(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        if _SPECIES_START_MARK in line.upper():
            return index + 1
    raise BurcatParseError("BURCAT.THR.txt missing Chemical Abstracts marker")


def _name_from_comment_block(comment_block: list[str], cas: str) -> str:
    for line in comment_block:
        stripped = line.strip()
        if not stripped or stripped == cas:
            continue
        return stripped[:80]
    return cas or "(comment-only species)"


def parse_burcat_thr(
    text: str,
    *,
    source_path: str = SOURCE_REL,
    source_sha256: str = "",
) -> BurcatFile:
    """Parse every NASA-7 polynomial and comment-only CAS stanza."""
    lines = text.splitlines()
    start = _species_section_start(lines)
    records: list[BurcatRecord] = []
    i = start
    header_line_count = start
    current_cas = ""
    current_cas_line: int | None = None
    current_comments: list[str] = []
    saw_poly_in_stanza = False
    pending_prose: list[tuple[int, str]] = []

    def flush_comment_only(end_line: int) -> None:
        nonlocal current_cas, current_cas_line, current_comments, saw_poly_in_stanza
        if saw_poly_in_stanza and any(line.strip() for _, line in pending_prose):
            records[-1].ambiguities.append({
                "kind": "unassigned_post_polynomial_prose",
                "note": "Published stanza tail, cross-reference or footer; no polynomial association inferred.",
                "source_lines": [n for n, _ in pending_prose],
                "text_as_published": [line for _, line in pending_prose],
            })
        pending_prose.clear()
        if not current_cas or saw_poly_in_stanza:
            current_cas = ""
            current_cas_line = None
            current_comments = []
            saw_poly_in_stanza = False
            return
        comment = list(current_comments)
        name = _name_from_comment_block(comment, current_cas)
        records.append(
            BurcatRecord(
                record_id="",
                name_as_published=name,
                cas_as_published=current_cas,
                formula="",
                phase="not_parsed",
                phase_ordinal=None,
                phase_as_published="",
                phase_card_as_published="",
                date_as_published="",
                calc_quality_as_published="",
                composition_slots=[],
                molecular_weight=PublishedNumber(as_published="", value=None),
                T_min_K=PublishedNumber(as_published="", value=None),
                T_max_K=PublishedNumber(as_published="", value=None),
                intervals=[],
                hf298_div_r=None,
                comment_block=comment,
                raw_header_line="",
                raw_coeff_lines=[],
                source_path=source_path,
                cas_line_number=None if current_cas_line is None else current_cas_line + 1,
                header_line_number=None,
                end_line_number=end_line,
                record_kind="comment_only",
                ambiguities=[
                    {
                        "kind": "comment_only_no_polynomial",
                        "cas_as_published": current_cas,
                        "note": (
                            "CAS stanza publishes a comment/HF298 note but no "
                            "NASA-7 4-line polynomial. Kept; not typed measured."
                        ),
                    }
                ],
            )
        )
        current_cas = ""
        current_cas_line = None
        current_comments = []
        saw_poly_in_stanza = False

    while i < len(lines):
        if is_nasa7_four_line_record(lines, i):
            header_line = lines[i]
            coeff_lines = [lines[i + 1], lines[i + 2], lines[i + 3]]
            parsed = parse_nasa7_header_line(header_line)
            coeffs, coeff_ambiguities = parse_nasa7_coefficient_lines(coeff_lines)
            high = coeffs[0:7]
            low = coeffs[7:14]
            hf = coeffs[14] if len(coeffs) > 14 else PublishedNumber(as_published="", value=None)
            ambiguities = list(parsed["ambiguities"]) + list(coeff_ambiguities)
            if any(line.strip() for _, line in pending_prose):
                ambiguities.append({
                    "kind": "cas_shared_across_prose_separated_polynomials",
                    "cas_as_published": current_cas,
                    "note": "Preceding stanza CAS retained as published; identity of later prose is not inferred.",
                })
            pending_prose.clear()
            if current_cas and not re.match(r"^\d", current_cas) and current_cas != "N/A":
                ambiguities.append(
                    {
                        "kind": "cas_identifier_nonstandard",
                        "cas_as_published": current_cas,
                    }
                )
            elif current_cas and current_cas not in {"N/A"} and not re.match(
                r"^\d{2,7}-\d{2}-\d$", current_cas.split()[0].rstrip("?")
            ):
                ambiguities.append(
                    {
                        "kind": "cas_identifier_nonstandard",
                        "cas_as_published": current_cas,
                    }
                )
            intervals = [
                Nasa7Interval(
                    range_as_published="high_T_first",
                    xml_range_tag="range_1000_to_Tmax",
                    a_coefficients=high,
                ),
                Nasa7Interval(
                    range_as_published="low_T_second",
                    xml_range_tag="range_Tmin_to_1000",
                    a_coefficients=low,
                ),
            ]
            name = str(parsed["name_as_published"])
            phase_char = str(parsed["phase_as_published"])
            phase_as_published, phase_normalized, phase_ambiguities = resolve_phase(
                name,
                phase_char=phase_char,
            )
            ambiguities.extend(phase_ambiguities)
            records.append(
                BurcatRecord(
                    record_id="",
                    name_as_published=name,
                    cas_as_published=current_cas,
                    formula=str(parsed["formula"]),
                    phase=phase_normalized,
                    phase_ordinal=None,
                    phase_as_published=phase_as_published,
                    phase_card_as_published=phase_char,
                    date_as_published=str(parsed["date_as_published"]),
                    calc_quality_as_published=str(parsed["calc_quality_as_published"]),
                    composition_slots=list(parsed["composition_slots"]),
                    molecular_weight=parsed["molecular_weight"],
                    T_min_K=parsed["T_min_K"],
                    T_max_K=parsed["T_max_K"],
                    intervals=intervals,
                    hf298_div_r=hf,
                    comment_block=list(current_comments),
                    raw_header_line=header_line,
                    raw_coeff_lines=list(coeff_lines),
                    source_path=source_path,
                    cas_line_number=(
                        None if current_cas_line is None else current_cas_line + 1
                    ),
                    header_line_number=i + 1,
                    end_line_number=i + 4,
                    record_kind="nasa7_polynomial",
                    ambiguities=ambiguities,
                )
            )
            saw_poly_in_stanza = True
            i += 4
            continue
        line = lines[i]
        if _is_cas_line(line):
            flush_comment_only(i)
            current_cas = line.strip()
            current_cas_line = i
            current_comments = [line]
            saw_poly_in_stanza = False
            i += 1
            continue
        if current_cas and not saw_poly_in_stanza:
            current_comments.append(line)
            i += 1
            continue
        if current_cas and saw_poly_in_stanza and not line.strip():
            pending_prose.append((i + 1, line))
            i += 1
            continue
        if current_cas and saw_poly_in_stanza and line.strip():
            current_comments.append(line)
            pending_prose.append((i + 1, line))
            i += 1
            continue
        i += 1
    flush_comment_only(len(lines))

    by_name: dict[str, list[int]] = defaultdict(list)
    for index, rec in enumerate(records):
        rec.record_id = f"BU-{index + 1:04d}"
        if rec.record_kind == "nasa7_polynomial":
            by_name[rec.name_as_published].append(index)
    by_phase: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, rec in enumerate(records):
        if rec.record_kind == "nasa7_polynomial" and rec.phase != "gas":
            by_phase[(rec.formula, rec.phase)].append(index)
    for indices in by_phase.values():
        if len(indices) < 2:
            continue
        for ordinal, j in enumerate(indices, start=1):
            records[j].phase_ordinal = ordinal
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
                    "note": "Distinct BURCAT.THR 4-line records; not merged.",
                }
            )

    footer_line_count = max(0, len(lines) - (records[-1].end_line_number if records else start))
    return BurcatFile(
        records=records,
        source_path=source_path,
        source_sha256=source_sha256,
        xml_crosscheck={},
        header_line_count=header_line_count,
        footer_line_count=footer_line_count,
    )


def _xml_text(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def parse_burcat_xml(path: Path) -> list[dict[str, Any]]:
    """Parse BURCAT_THR.xml phases for cross-check (2005 snapshot)."""
    tree = ET.parse(path)
    root = tree.getroot()
    phases: list[dict[str, Any]] = []
    for specie in root.findall("specie"):
        cas = specie.get("CAS") or ""
        for phase_el in specie.findall("phase"):
            coeffs_el = phase_el.find("coefficients")
            high: list[str] = []
            low: list[str] = []
            hf = ""
            if coeffs_el is not None:
                high_el = coeffs_el.find("range_1000_to_Tmax")
                low_el = coeffs_el.find("range_Tmin_to_1000")
                if high_el is not None:
                    high = [_xml_text(c) for c in high_el.findall("coef")]
                if low_el is not None:
                    low = [_xml_text(c) for c in low_el.findall("coef")]
                hf_el = coeffs_el.find("hf298_div_r")
                hf = _xml_text(hf_el)
            temp = phase_el.find("temp_limit")
            t_low = temp.get("low") if temp is not None else ""
            t_high = temp.get("high") if temp is not None else ""
            phases.append(
                {
                    "cas": cas,
                    "formula": _xml_text(phase_el.find("formula")),
                    "phase": _xml_text(phase_el.find("phase")),
                    "T_min_K": t_low or "",
                    "T_max_K": t_high or "",
                    "calc_quality": _xml_text(phase_el.find("calc_quality")),
                    "molecular_weight": _xml_text(phase_el.find("molecular_weight")),
                    "high_T": high,
                    "low_T": low,
                    "hf298_div_r": hf,
                }
            )
    return phases


def _float_or_none(token: str) -> float | None:
    text = token.strip().replace("D", "E").replace("d", "e")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _coeff_values_match(left: list[str], right: list[PublishedNumber]) -> bool:
    if len(left) != len(right):
        return False
    for xml_tok, pub in zip(left, right):
        if xml_tok.strip() == pub.as_published.strip():
            continue
        xml_val = _float_or_none(xml_tok)
        if xml_val is None or pub.value is None:
            return False
        if xml_val != pub.value:
            return False
    return True


def crosscheck_xml(
    records: list[BurcatRecord],
    xml_phases: list[dict[str, Any]],
) -> dict[str, Any]:
    """Match XML phases onto THR records. Mismatches are recorded, not fixed."""
    by_key: dict[tuple[str, str, str, str], list[BurcatRecord]] = defaultdict(list)
    by_short: dict[tuple[str, str, str, str], list[BurcatRecord]] = defaultdict(list)
    for rec in records:
        if rec.record_kind != "nasa7_polynomial":
            continue
        tmin = rec.T_min_K.as_published
        tmax = rec.T_max_K.as_published
        phase = rec.phase_card_as_published or rec.phase_as_published
        by_key[(rec.name_as_published, phase, tmin, tmax)].append(rec)
        by_short[(rec.name_as_published[:18].rstrip(), phase, tmin, tmax)].append(rec)

    matched = 0
    mismatched: list[dict[str, Any]] = []
    xml_only: list[dict[str, str]] = []
    for phase in xml_phases:
        formula = str(phase["formula"])
        key = (formula, str(phase["phase"]), str(phase["T_min_K"]), str(phase["T_max_K"]))
        short = (
            formula[:18].rstrip(),
            str(phase["phase"]),
            str(phase["T_min_K"]),
            str(phase["T_max_K"]),
        )
        candidates = by_key.get(key) or by_short.get(short) or []
        if not candidates:
            xml_only.append(
                {
                    "formula": formula,
                    "phase": str(phase["phase"]),
                    "cas": str(phase["cas"]),
                    "T_min_K": str(phase["T_min_K"]),
                    "T_max_K": str(phase["T_max_K"]),
                }
            )
            continue
        rec = candidates[0]
        high_ok = _coeff_values_match(list(phase["high_T"]), rec.intervals[0].a_coefficients)
        low_ok = _coeff_values_match(list(phase["low_T"]), rec.intervals[1].a_coefficients)
        hf_ok = True
        if rec.hf298_div_r is not None:
            hf_tok = str(phase["hf298_div_r"])
            if hf_tok.strip() != rec.hf298_div_r.as_published.strip():
                xml_val = _float_or_none(hf_tok)
                hf_ok = xml_val is not None and xml_val == rec.hf298_div_r.value
        if high_ok and low_ok and hf_ok:
            matched += 1
            continue
        rec.ambiguities.append(
            {
                "kind": "xml_coefficient_mismatch",
                "xml_formula": formula,
                "xml_cas": phase["cas"],
                "note": (
                    "BURCAT_THR.xml (2005 snapshot) coefficients differ from "
                    "this BURCAT.THR.txt record. THR tokens retained."
                ),
            }
        )
        mismatched.append(
            {
                "record_id": rec.record_id,
                "name_as_published": rec.name_as_published,
                "xml_formula": formula,
            }
        )

    return {
        "xml_phase_count": len(xml_phases),
        "matched_count": matched,
        "mismatch_count": len(mismatched),
        "xml_only_count": len(xml_only),
        "xml_only_head": xml_only[:20],
        "mismatched_head": mismatched[:20],
        "note": (
            "XML is the Sept 2005 Thermodyne2XML snapshot (READ.ME: outdated). "
            "THR is current to 3 January 2023. Cross-check only; THR wins."
        ),
    }


def peer_formula_sets(
    *,
    janaf_manifest: Path | None = None,
    glenn_manifest: Path | None = None,
) -> tuple[set[str], set[str]]:
    janaf_path = janaf_manifest or (
        ROOT / "data" / "literature" / "compilations" / "janaf" / "manifest.yaml"
    )
    glenn_path = glenn_manifest or (
        ROOT / "data" / "literature" / "compilations" / "nasa-glenn" / "manifest.yaml"
    )
    janaf: set[str] = set()
    glenn: set[str] = set()
    if janaf_path.is_file():
        payload = yaml.safe_load(janaf_path.read_text(encoding="utf-8")) or {}
        for entry in payload.get("entries") or []:
            formula = str(entry.get("formula") or "").strip()
            if formula:
                janaf.add(formula)
    if glenn_path.is_file():
        payload = yaml.safe_load(glenn_path.read_text(encoding="utf-8")) or {}
        for entry in payload.get("entries") or []:
            formula = str(entry.get("formula") or "").strip()
            if formula:
                glenn.add(formula)
    return janaf, glenn


def formulas_absent_from_janaf_and_glenn(
    records: Iterable[BurcatRecord],
    janaf_formulas: Iterable[str],
    glenn_formulas: Iterable[str],
) -> list[dict[str, Any]]:
    peers = set(janaf_formulas) | set(glenn_formulas)
    seen: dict[str, dict[str, Any]] = {}
    for rec in records:
        if rec.record_kind != "nasa7_polynomial":
            continue
        formula = rec.formula.strip()
        if not formula or formula in peers:
            continue
        row = seen.setdefault(
            formula,
            {
                "formula": formula,
                "record_count": 0,
                "names_head": [],
                "record_ids_head": [],
            },
        )
        row["record_count"] += 1
        if rec.name_as_published not in row["names_head"] and len(row["names_head"]) < 4:
            row["names_head"].append(rec.name_as_published)
        if rec.record_id not in row["record_ids_head"] and len(row["record_ids_head"]) < 4:
            row["record_ids_head"].append(rec.record_id)
    return [seen[key] for key in sorted(seen)]


def record_document(record: BurcatRecord) -> dict[str, Any]:
    return record.to_dict()


def load_record_document(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    manifest_path = path or (COMPILATION_ROOT / "manifest.yaml")
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise BurcatParseError(f"{manifest_path}: manifest is not a mapping")
    return payload


def iter_record_paths(records_dir: Path | None = None) -> Iterator[Path]:
    root = records_dir or (COMPILATION_ROOT / "records")
    yield from sorted(root.glob("BU-*.json"))


def load_all_record_documents(
    records_dir: Path | None = None,
) -> list[dict[str, Any]]:
    return [load_record_document(path) for path in iter_record_paths(records_dir)]


__all__ = [
    "COMPILATION_ROOT",
    "COMPILATION_SOURCE",
    "ELEMENT_SYMBOLS",
    "MANIFEST_SCHEMA",
    "NASA7_COEFFICIENT_COUNT",
    "SCHEMA_VERSION",
    "SIDECAR_REL",
    "SOURCE_ID",
    "SOURCE_REL",
    "XML_REL",
    "BurcatFile",
    "BurcatParseError",
    "BurcatRecord",
    "coverage_by_element",
    "crosscheck_xml",
    "feedstock_element_symbols",
    "formulas_absent_from_janaf_and_glenn",
    "iter_record_paths",
    "load_all_record_documents",
    "load_manifest",
    "load_record_document",
    "parse_burcat_thr",
    "parse_burcat_xml",
    "peer_formula_sets",
    "published_float_pairs",
    "record_document",
]
