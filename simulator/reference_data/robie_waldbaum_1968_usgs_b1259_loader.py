"""Robie & Waldbaum 1968 (USGS Bulletin 1259) compilation loader.

Native transcription of the printed tables. No unit conversion, no rounding,
no interpolation, no gap filling. OCR tokens are kept as printed; identity
relations are detectors only.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
import subprocess
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPILATION_ROOT = ROOT / "data/literature/compilations/robie-waldbaum-1968-usgs-b1259"
RECORDS_DIR = COMPILATION_ROOT / "records"
SOURCE_ID = "robie-waldbaum-1968-usgs-b1259"
PDF_SHA256 = "013abbde1aef291c7492f2eaf7ab552619699e558d741ae5f39a87830e080e83"
PDF_URL = "https://pubs.usgs.gov/bul/1259/report.pdf"
SCHEMA_VERSION = "literature_compilation.v1"
ROLE = {
    "kind": "assessed_thermodynamic_functions",
    "engine_reference_input": True,
    "validation_measurement": False,
    "scoring_eligible": False,
    "battery_refusal": "gibbs_table_not_runtime_observable",
    "circularity_warning": "Do not validate an engine against a compilation it consumes.",
}
R_CAL = Decimal("1.98717")
LN10 = Decimal("2.302585092994046")
HIGH_T_COLUMNS = (
    "temperature",
    "H_minus_H298",
    "entropy",
    "gibbs_function",
    "delta_f_H",
    "delta_f_G",
    "log_Kf",
)
CLEAN_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
HEADER_WEIGHT = re.compile(
    r"(?P<name>.*?)\s+"
    r"(?:GRA[MNKP][A-Z]{0,2}|GRAM\.?)\s+"
    r"FOR['’.]?MULA\s+"
    r"(?:WEIGHT|HEIGHT|WEIGTH|WE1GHT)\s+"
    r"(?P<gfw>\S+)",
    re.I,
)
IMAGE_VERIFIED_METADATA = {
    91: {"phase": "Crystals 298.15° to 1000°K."},
    96: {
        "phase": (
            "α crystals 298.15° to 875°K. β crystals 875° to melting point 1153°K. "
            "Liquid 1153° to 1200°K."
        )
    },
    100: {"formula_as_published": "AlO(OH)"},
    109: {
        "name_as_published": "PORTLANDITE",
        "formula_as_published": "Ca(OH)2",
        "phase": "Crystals 298.15° to 700°K.",
    },
    134: {"name_as_published": "BUNSENITE"},
    145: {"phase": "α tridymite 298.15° to 390°K. β tridymite 390° to 2000°K."},
    153: {"phase": "Crystals 298.15° to 1300°K."},
    206: {"formula_as_published": "Ca5(PO4)3OH"},
    233: {"phase": "Crystals 298.15° to 1400°K."},
    237: {"phase": "Crystals 298.15° to 1400°K."},
    238: {"phase": "Crystals 298.15° to 1400°K."},
}
IMAGE_VERIFIED_LAYOUT_RAWS = {
    (91, "phase"): "Crystals 298.15° to 1000°K.",
    (96, "phase"): (
        "a crystals 298.15° to 875°K.       0 crystals 875° to melting\n\n"
        "                   point 1153°K.     Liquid 1153° to 1200°K."
    ),
    (109, "phase"): "Crystals 298.15° to 700°K.",
    (134, "name_as_published"): "8UNSENITE",
    (145, "phase"): "a tridymite 298. 15° to 390°K.       & tridymite 390° to 2000°K.",
    (153, "phase"): "Crystals 298. 15°' to 1300°K.",
    (233, "phase"): "Crystals 298.15° to 1400°K.",
    (237, "phase"): "Crystals 298.15° to 1400°K.",
    (238, "phase"): "Crystals 298.15° to 1400°K.",
}
IMAGE_VERIFIED_TEMPERATURES = {
    109: {"AGO": "400", "(SOO": "600"},
    145: {"20CO": "2000"},
    162: {"15CO": "1500"},
    165: {"6CO": "600", "12CO": "1200"},
    206: [str(value) for value in range(400, 1501, 100)],
}
IMAGE_VERIFIED_298K_IDENTITIES = {
    'Al\'H"1" aqueous ion': ("Al+++ aqueous ion", "Al+++", 17),
    'Ca"1"1" aqueous ion': ("Ca++ aqueous ion", "Ca++", 17),
    'Cc"1"*"*" aqueous ion': ("Ce+++ aqueous ion", "Ce+++", 17),
    "Hg2+* aqueous ion": ("Hg₂++ aqueous ion", "Hg₂++", 18),
    'K"1" aqueous ion': ("K+ aqueous ion", "K+", 18),
    'U"1"1"1"1" aqueous ion': ("U++++ aqueous ion", "U++++", 20),
    "V 1 ' ' aqueous ion": ("V+++ aqueous ion", "V+++", 20),
    'Zn"1"1" aqueous ion': ("Zn++ aqueous ion", "Zn++", 20),
}
IMAGE_VERIFIED_CELLS = {
    50: [(1700, 1, "delta_f_H", ".000")],
    70: [(1900, 1, "gibbs_function", "63.04")],
    85: [
        (900, 1, "gibbs_function", "50.09"),
        (1600, 1, "gibbs_function", "55.02"),
    ],
    91: [
        (500, 1, "delta_f_H", "-43.056"),
        (600, 1, "delta_f_H", "-43.788"),
        (700, 1, "delta_f_H", "-44.418"),
        (900, 1, "delta_f_H", "-71.064"),
        (1000, 1, "delta_f_H", "-71.136"),
    ],
    96: [
        (500, 1, "log_Kf", "10.691"),
        (800, 1, "delta_f_H", "-41.603"),
        (800, 1, "delta_f_G", "-23.337"),
        (800, 1, "log_Kf", "6.375"),
    ],
    107: [(298.15, 1, "gibbs_function", "51.06")],
    132: [(1500, 1, "gibbs_function", "66.04")],
    146: [(1700, 1, "entropy", "36.03")],
    165: [(2000, 1, "gibbs_function", "66.06")],
    168: [
        (703, 1, "H_minus_H298", "6.330"),
        (703, 1, "log_Kf", "5.792"),
    ],
    180: [
        (1424, 2, "entropy", "48.05"),
        (1691, 1, "gibbs_function", "34.02"),
    ],
    181: [
        (298.15, 1, "delta_f_H", "-268.700"),
        (298.15, 1, "delta_f_G", "-256.008"),
    ],
    191: [
        (temperature, 1, field, printed)
        for temperature, delta_h, delta_g in (
            (298.15, "-212.521", "-195.045"),
            (400, "-212.387", "-189.094"),
            (500, "-212.107", "-183.298"),
            (600, "-211.878", "-177.631"),
            (700, "-211.470", "-171.883"),
        )
        for field, printed in (("delta_f_H", delta_h), ("delta_f_G", delta_g))
    ],
    209: [
        (298.15, 1, "delta_f_H", "-619.390"),
        (298.15, 1, "delta_f_G", "-584.134"),
    ],
}
EXPECTED_298K_PAGE_COUNTS = (24, 24, 24, 24, 25, 24, 24, 24, 21, 23, 21, 22, 23, 23, 8)
PRINTED_PHASE_LABELS = {
    "Black",
    "Crystal",
    "Dimeric",
    "Fictive",
    "Hexagonal",
    "Ideal gas",
    "Liquid",
    "Metastable",
    "Quartz form",
    "Red",
    "Red V",
    "Rhombohedral",
    "Weal gas",
    "White",
    "Yellow",
}


class TemperatureNotOnPrintedGrid(LookupError):
    """Typed refusal: no interpolation, extrapolation, or zero default."""


def numeric_token(token: str) -> Decimal | None:
    """Parse a clean published token. Empty stays empty; never manufacture a value."""
    if token in ("", None):
        return None
    stripped = str(token).strip().rstrip("*'`,;")
    if not CLEAN_NUMBER.fullmatch(stripped):
        raise ValueError(f"non-numeric published token: {token!r}")
    return Decimal(stripped)


def _is_numberish(tok: str) -> bool:
    t = tok.strip("*'`,;\"")
    if not t or t in {".", "+", "-"}:
        return False
    letters = sum(c.isalpha() for c in t)
    digits = sum(c.isdigit() for c in t)
    return digits >= 1 and letters <= 3 and not t.isalpha()


def _token_spans(line: str) -> list[tuple[int, int, str]]:
    return [(match.start(), match.end(), match.group()) for match in re.finditer(r"\S+", line)]


def _group_split_decimal_spans(
    line: str, spans: list[tuple[int, int, str]]
) -> list[tuple[int, int, str]]:
    """Keep split OCR decimals as one raw, suspect cell without silently repairing them."""
    grouped: list[tuple[int, int, str]] = []
    i = 0
    while i < len(spans):
        start, end, token = spans[i]
        if i + 1 < len(spans):
            next_start, next_end, next_token = spans[i + 1]
            close = next_start - end <= 3
            if close and (
                (token.endswith(".") and re.fullmatch(r"\d+[A-Za-z]?", next_token))
                or (_is_numberish(token) and next_token.startswith(".") and _is_numberish(next_token))
            ):
                grouped.append((start, next_end, line[start:next_end]))
                i += 2
                continue
        grouped.append((start, end, token))
        i += 1
    return grouped


def _cell(raw: str, blank_reason: str = "blank_as_published") -> dict:
    return inspect_token(raw) if raw else _empty_cell(blank_reason)


def _avoid_splitting_token(line: str, boundary: int) -> int:
    if boundary <= 0 or boundary >= len(line):
        return boundary
    if line[boundary - 1].isspace() or line[boundary].isspace():
        return boundary
    while boundary > 0 and not line[boundary - 1].isspace():
        boundary -= 1
    return boundary


def inspect_token(raw: str) -> dict:
    published = raw
    stripped = raw.strip()
    footnotes: list[str] = []
    while stripped and stripped[-1] in "*†‡§¶":
        footnotes.append(stripped[-1])
        stripped = stripped[:-1]
    suspect = False
    reasons: list[str] = []
    if any(c.isalpha() and c not in "eE" for c in stripped):
        suspect = True
        reasons.append("letter_in_numeric_token")
    if any(c in "*^~,'\"" for c in stripped):
        suspect = True
        reasons.append("punctuation_ocr")
    value = None
    token_for_parse = stripped
    if CLEAN_NUMBER.fullmatch(token_for_parse):
        try:
            value = float(Decimal(token_for_parse))
        except (InvalidOperation, ValueError):
            suspect = True
            reasons.append("decimal_parse_failed")
    else:
        suspect = True
        reasons.append("non_clean_numberish" if _is_numberish(token_for_parse) else "not_numeric")
    return {
        "as_published": published,
        "value": value,
        "ocr_suspect": suspect,
        "footnote_markers": footnotes,
        "ocr_reasons": reasons,
    }


def _empty_cell(reason: str) -> dict:
    return {
        "as_published": "",
        "value": None,
        "ocr_suspect": True,
        "footnote_markers": [],
        "ocr_reasons": [reason],
    }


def _identity_notes(row: dict) -> list[str]:
    notes: list[str] = []
    T = (row.get("temperature") or {}).get("value")
    H = (row.get("H_minus_H298") or {}).get("value")
    S = (row.get("entropy") or {}).get("value")
    gef = (row.get("gibbs_function") or {}).get("value")
    dG = (row.get("delta_f_G") or {}).get("value")
    logk = (row.get("log_Kf") or {}).get("value")
    if T and S is not None and gef is not None and H is not None:
        pred = S - (1000.0 * H) / T
        delta = abs(pred - gef)
        if delta > 0.15:
            notes.append(f"gef_identity |gef-(S-1000H/T)|={delta:.4f} at T={T}")
    if T and dG is not None and logk is not None:
        denom = float(R_CAL) * T * float(LN10)
        if denom:
            pred = -1000.0 * dG / denom
            delta = abs(pred - logk)
            if delta > 0.08:
                notes.append(f"logKf_identity |logK+1000 dG/(RT ln10)|={delta:.4f} at T={T}")
    return notes


def _image_correction(
    record: dict, cell: dict, field: str, printed: str, quote: str, ordinal: int
) -> None:
    correction_id = f"b1259-p{record['pdf_page']}-{field}-{ordinal:02d}"
    raw = cell.get("as_published", "")
    cell.update(
        {
            "layout_as_extracted": raw,
            "as_published": printed,
            "value": float(Decimal(printed)),
            "ocr_suspect": False,
            "ocr_reasons": [],
            "correction_id": correction_id,
        }
    )
    record.setdefault("corrections", []).append(
        {
            "correction_id": correction_id,
            "pdf_page": record["pdf_page"],
            "field": field,
            "layout_as_extracted": raw,
            "printed_token": printed,
            "evidence": quote,
        }
    )


def _apply_image_verified_corrections(record: dict) -> None:
    pdf_page = record["pdf_page"]
    metadata = IMAGE_VERIFIED_METADATA.get(pdf_page, {})
    for field, printed in metadata.items():
        raw = record.get(field, "")
        record[field] = printed
        if field == "formula_as_published":
            record["formula"] = printed
        record.setdefault("corrections", []).append(
            {
                "correction_id": f"b1259-p{pdf_page}-{field}",
                "pdf_page": pdf_page,
                "field": field,
                "layout_as_extracted": IMAGE_VERIFIED_LAYOUT_RAWS.get(
                    (pdf_page, field), raw
                ),
                "printed_token": printed,
                "evidence": f"PDF page {pdf_page} image visibly prints {printed!r}",
            }
        )

    temperature_spec = IMAGE_VERIFIED_TEMPERATURES.get(pdf_page)
    blank_temperatures = iter(temperature_spec if isinstance(temperature_spec, list) else [])
    for ordinal, row in enumerate(record.get("rows") or [], 1):
        if row.get("kind") not in {"data", "grid_refusal"}:
            continue
        temperature = row.get("temperature") or {}
        raw_temperature = temperature.get("as_published", "")
        printed_temperature = None
        if isinstance(temperature_spec, dict):
            printed_temperature = temperature_spec.get(raw_temperature)
        elif isinstance(temperature_spec, list) and row.get("kind") == "grid_refusal" and not raw_temperature:
            printed_temperature = next(blank_temperatures, None)
        if printed_temperature:
            _image_correction(
                record,
                temperature,
                "temperature",
                printed_temperature,
                f"PDF page {pdf_page} image visibly prints T={printed_temperature} K on this row",
                ordinal,
            )
            row["kind"] = "data"
            row.pop("grid_refusal_reason", None)

        if pdf_page in {100, 109, 206}:
            for field in ("delta_f_H", "delta_f_G"):
                cell = row.get(field) or {}
                if cell.get("value") is not None and cell["value"] > 0 and not cell.get("ocr_suspect"):
                    printed = f"-{cell['as_published']}"
                    _image_correction(
                        record,
                        cell,
                        field,
                        printed,
                        f"PDF page {pdf_page} image visibly prints the leading minus sign in {printed}",
                        ordinal,
                    )

    occurrence_by_temperature: dict[float, int] = {}
    for ordinal, row in enumerate(record.get("rows") or [], 1):
        if row.get("kind") not in {"data", "grid_refusal"}:
            continue
        temperature = (row.get("temperature") or {}).get("value")
        if temperature is None:
            continue
        occurrence_by_temperature[temperature] = occurrence_by_temperature.get(temperature, 0) + 1
        occurrence = occurrence_by_temperature[temperature]
        for wanted_t, wanted_occurrence, field, printed in IMAGE_VERIFIED_CELLS.get(pdf_page, []):
            if temperature == wanted_t and occurrence == wanted_occurrence:
                _image_correction(
                    record,
                    row[field],
                    field,
                    printed,
                    f"PDF page {pdf_page} image visibly prints {field}={printed} at T={wanted_t} K",
                    ordinal,
                )

    rows = [row for row in record.get("rows") or [] if row.get("kind") in {"data", "grid_refusal"}]
    record["row_count"] = len(rows)
    record["lookup_grid_count"] = sum(row.get("kind") == "data" for row in rows)
    record["grid_refusals"] = [
        {
            "raw_temperature_token": (row.get("temperature") or {}).get("as_published", ""),
            "source_layout_line": row.get("source_layout_line", ""),
            "reason": row.get("grid_refusal_reason"),
        }
        for row in rows
        if row.get("kind") == "grid_refusal"
    ]
    identity: list[str] = []
    for row in rows:
        notes = _identity_notes(row)
        if notes:
            row["identity_disagreements"] = notes
            identity.extend(notes)
        else:
            row.pop("identity_disagreements", None)
    record["identity_disagreements"] = identity


def _clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip(" .")
    name = re.sub(r"^\d+\s+", "", name)
    name = re.sub(r"^(?:THERMO\s*)?DYNAMIC PROPERTIES OF MINERALS\s*", "", name, flags=re.I)
    name = re.sub(r"^PROPERTIES AT HIGH TEMPERATURES\s*\d*\s*", "", name, flags=re.I)
    return name.strip(" .")


def _high_t_column_ranges(lines: list[str]) -> dict[str, tuple[int, int | None]]:
    """Derive fixed table columns from complete rows, independent of row assignment."""
    candidates: list[list[tuple[int, int, str]]] = []
    partial_candidates: list[list[tuple[int, int, str]]] = []
    in_table = False
    for line in lines:
        if re.search(r"FORMATION FROM", line, re.I) or (
            re.search(r"T\s+2(?:9|<)8", line, re.I)
            and re.search(r"ENTHALPY|FREE ENERGY|LOG K", line, re.I)
        ):
            in_table = True
            continue
        if not in_table:
            continue
        spans = _group_split_decimal_spans(line, _token_spans(line))
        numeric = [span for span in spans if _is_numberish(span[2])]
        if numeric and numeric[0][0] <= 8 and 2 <= len(numeric) <= len(HIGH_T_COLUMNS):
            partial_candidates.append(numeric)
            if len(numeric) == len(HIGH_T_COLUMNS):
                candidates.append(numeric)
    if candidates:
        boundaries = [
            int(statistics.median((row[index][1] + row[index + 1][0]) // 2 for row in candidates))
            for index in range(len(HIGH_T_COLUMNS) - 1)
        ]
    else:
        header = next(
            (line for line in lines if re.search(r"TEMP\.", line, re.I) and re.search(r"H\s*-H", line, re.I)),
            "",
        )
        formation = next(
            (
                line
                for line in lines
                if re.search(r"ENTHALPY", line, re.I)
                and re.search(r"FREE ENERGY", line, re.I)
                and re.search(r"LOG K", line, re.I)
            ),
            "",
        )
        h_match = re.search(r"H\s*-H", header, re.I)
        g_match = re.search(r"-\S*G", header, re.I)
        between_h_and_g = header[h_match.end() : g_match.start()] if h_match and g_match else ""
        s_match = re.search(r"\S+", between_h_and_g)
        enthalpy_match = re.search(r"ENTHALPY", formation, re.I)
        free_match = re.search(r"FREE ENERGY", formation, re.I)
        logk_match = re.search(r"LOG K", formation, re.I)
        anchors: list[int | None] = [None] * len(HIGH_T_COLUMNS)
        anchors[0] = 0
        if partial_candidates:
            widths = [len(row) for row in partial_candidates]
            widest = max(set(widths), key=lambda width: (widths.count(width), -width))
            widest_rows = [row for row in partial_candidates if len(row) == widest]
            for index in range(widest):
                anchors[index] = int(statistics.median(row[index][0] for row in widest_rows))
        if h_match:
            anchors[1] = anchors[1] if anchors[1] is not None else h_match.start()
        if s_match and h_match:
            anchors[2] = anchors[2] if anchors[2] is not None else h_match.end() + s_match.start()
        if g_match:
            anchors[3] = anchors[3] if anchors[3] is not None else g_match.start()
        t298_matches = list(re.finditer(r"\bT\s+2(?:9|<)8\b", formation, re.I))
        if len(t298_matches) >= 2:
            if anchors[1] is None:
                anchors[1] = t298_matches[0].start()
            if anchors[3] is None:
                anchors[3] = t298_matches[1].start()
            middle_t = re.search(
                r"\bT\b",
                formation[t298_matches[0].end() : t298_matches[1].start()],
                re.I,
            )
            if middle_t and anchors[2] is None:
                anchors[2] = t298_matches[0].end() + middle_t.start()
        for index, match in ((4, enthalpy_match), (5, free_match), (6, logk_match)):
            if match and anchors[index] is None:
                anchors[index] = match.start()
        if any(anchor is None for anchor in anchors):
            raise ValueError("could not derive fixed high-temperature table columns")
        numeric_anchors = [int(anchor) for anchor in anchors if anchor is not None]
        boundaries = []
        for index in range(len(HIGH_T_COLUMNS) - 1):
            if partial_candidates and index < widest - 1:
                boundary = int(
                    statistics.median(
                        (row[index][1] + row[index + 1][0]) // 2 for row in widest_rows
                    )
                )
            elif partial_candidates and index == widest - 1:
                boundary = int(
                    statistics.median(
                        (row[index][1] + numeric_anchors[index + 1]) // 2
                        for row in widest_rows
                    )
                )
            else:
                boundary = (numeric_anchors[index] + numeric_anchors[index + 1]) // 2
            boundaries.append(boundary)
    starts = [0, *boundaries]
    ends: list[int | None] = [*boundaries, None]
    return dict(zip(HIGH_T_COLUMNS, zip(starts, ends)))


def _parse_high_t_rows(lines: list[str]) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    identity: list[str] = []
    column_ranges = _high_t_column_ranges(lines)
    in_table = False
    for ln in lines:
        if in_table and re.search(r"\b(?:MELTING|BOILING)\s+POINT\b", ln, re.I):
            break
        if in_table and (
            re.match(r"^\s*HEAT OF (?:FUSION|VAPOR)", ln, re.I)
            or re.search(r"\bMOLAR VOLUME\b", ln, re.I)
        ):
            break
        if in_table and re.match(
            r"^\s*(?:REFERENCES?|RFFERENCCS|COMPILED|TRANSITIONS IN REFERENCE)\b", ln, re.I
        ):
            break
        if re.search(r"FORMATION FROM", ln, re.I) or (
            re.search(r"T\s+2(?:9|<)8", ln, re.I)
            and re.search(r"ENTHALPY|FREE ENERGY|LOG K", ln, re.I)
        ):
            in_table = True
            continue
        if not in_table:
            continue
        spans = _group_split_decimal_spans(ln, _token_spans(ln))
        if not spans:
            continue
        if spans[0][2].upper().startswith("UNCERT"):
            row = {
                "kind": "uncertainty",
                "source_layout_line": ln,
                "source_column_spans": {
                    field: [start, len(ln) if end is None else end]
                    for field, (start, end) in column_ranges.items()
                },
            }
            for field in HIGH_T_COLUMNS[1:]:
                start, end = column_ranges[field]
                raw = ln[start:end].strip()
                if raw:
                    row[field] = inspect_token(raw)
            rows.append(row)
            continue

        first_start, first_end, first_token = spans[0]
        leading_temperature = first_start <= 8
        numeric_spans = [span for span in spans if _is_numberish(span[2])]
        if not leading_temperature and len(numeric_spans) < 5:
            continue

        value_spans = [span for span in spans[1:] if _is_numberish(span[2])] if leading_temperature else numeric_spans
        temperature_raw = first_token if leading_temperature else ""
        if (
            leading_temperature
            and inspect_token(temperature_raw)["value"] is None
            and len(value_spans) < 3
        ):
            continue
        t_cell = _cell(temperature_raw, "temperature_absent_in_ocr_layout")
        T = t_cell.get("value")
        clean_temperature = (
            T is not None
            and not t_cell.get("ocr_suspect")
            and 50 <= T <= 2500
            and not (T < 250 and "." not in temperature_raw and abs(T - 298.15) > 1)
        )
        row = {
            "kind": "data" if clean_temperature else "grid_refusal",
            "source_layout_line": ln,
            "column_token_count": 0,
            "temperature": t_cell,
            "source_column_spans": {
                field: [start, len(ln) if end is None else end]
                for field, (start, end) in column_ranges.items()
            },
        }
        if leading_temperature:
            row["source_column_spans"]["temperature"] = [first_start, first_end]
        for col in HIGH_T_COLUMNS[1:]:
            start, end = column_ranges[col]
            raw = ln[start:end].strip()
            row[col] = _cell(raw, "column_absent_in_ocr")
        if (
            leading_temperature
            and T is not None
            and abs(T - 298.15) < 0.01
            and value_spans
            and value_spans[0][0] <= 12
        ):
            start, end, raw = value_spans[0]
            row["H_minus_H298"] = inspect_token(raw)
            row["source_column_spans"]["H_minus_H298"] = [start, end]
        row["column_token_count"] = sum(
            bool((row.get(col) or {}).get("as_published")) for col in HIGH_T_COLUMNS
        )
        missing_columns = [
            col for col in HIGH_T_COLUMNS[1:] if not row[col]["as_published"]
        ]
        if missing_columns:
            row["ocr_suspect_row"] = True
            row["missing_columns"] = missing_columns
        if not clean_temperature:
            row["ocr_suspect_row"] = True
            row["grid_refusal_reason"] = (
                "temperature token is absent or ambiguous in the OCR layout; "
                "row is retained but not admitted to lookup"
            )
        notes = _identity_notes(row)
        if notes:
            row["identity_disagreements"] = notes
            row["ocr_suspect_row"] = True
            identity.extend(notes)
        rows.append(row)
    return rows, identity


def parse_high_t_page(pdf_page: int, text: str) -> dict:
    bulletin_page = pdf_page - 6
    lines = text.splitlines()
    name = ""
    gfw = None
    formula = ""
    phase_notes: list[str] = []
    after_header = False
    for ln in lines[:16]:
        m = HEADER_WEIGHT.search(ln)
        if m:
            name = _clean_name(m.group("name"))
            gfw = inspect_token(m.group("gfw"))
            after_header = True
            break
    if gfw is None:
        for ln in lines[:8]:
            s = ln.strip()
            if CLEAN_NUMBER.fullmatch(s) and 1 < float(s) < 1000:
                gfw = inspect_token(s)
                break
    after_header = False
    for ln in lines:
        if HEADER_WEIGHT.search(ln):
            after_header = True
            continue
        s = ln.strip()
        if not s:
            continue
        if re.search(r"FORMATION FROM|FCRMATIDN FROM|TEMP\.|DEG K|OEG K|DEC K", s, re.I):
            if formula or phase_notes:
                break
            continue
        if ":" in s and not s.upper().startswith("STD"):
            left, right = s.split(":", 1)
            if not formula:
                formula = re.sub(r"\s+", " ", left).strip()
            note = re.sub(r"\s+", " ", right).strip()
            if note:
                phase_notes.append(note)
        elif after_header:
            if not re.search(r"FORMATION|TEMP\.|DEG K|OEG K|GRAM FORMULA", s, re.I):
                phase_notes.append(re.sub(r"\s+", " ", s))
        if formula and len(phase_notes) >= 3:
            break
    rows, identity = _parse_high_t_rows(lines)
    footer: dict[str, str] = {}
    blob = "\n".join(lines)

    def grab(pat: str, key: str) -> None:
        m = re.search(pat, blob, re.I | re.S)
        if m:
            footer[key] = re.sub(r"\s+", " ", m.group(1)).strip()

    grab(r"MELTING POINT\s+(\S+(?:\s+\S+)?)\s+(?:DEG|OEG|DEC|PEG)\s*K", "melting_point_K_as_published")
    grab(r"BOILING POINT\s+(\S+(?:\s+\S+)?)\s+(?:DEG|OEG|DEC|PEG)\s*K", "boiling_point_K_as_published")
    grab(r"HEAT OF FUSION\s+(\S+)\s+KCAL", "heat_of_fusion_kcal_as_published")
    grab(r"HEAT OF VAPOR\.?\s+(\S+)\s+KCAL", "heat_of_vapor_kcal_as_published")
    grab(r"MOLAR VOLUME\s+(\S+)\s+CAL/BAR", "molar_volume_cal_per_bar_as_published")
    grab(r"COMPILED\s+(\S+)", "compiled_date_as_published")
    refs = re.findall(r"REFERENCES?\s+([0-9][0-9\s]+)", blob, re.I)
    if refs:
        footer["references_as_published"] = " ".join(refs[0].split())
    data_rows = [r for r in rows if r.get("kind") in {"data", "grid_refusal"}]
    lookup_rows = [r for r in rows if r.get("kind") == "data"]
    grid_refusals = [
        {
            "raw_temperature_token": (r.get("temperature") or {}).get("as_published", ""),
            "source_layout_line": r.get("source_layout_line", ""),
            "reason": r.get("grid_refusal_reason"),
        }
        for r in rows
        if r.get("kind") == "grid_refusal"
    ]
    ambiguities = list(identity)
    if not name:
        ambiguities.append("name_header_absent_or_truncated_in_ocr; not inferred from neighbors")
    if not formula:
        ambiguities.append("formula_line_absent_or_truncated_in_ocr; not inferred")
    ocr_n = sum(
        1
        for r in data_rows
        for col in HIGH_T_COLUMNS
        if r.get(col, {}).get("ocr_suspect")
    )
    record = {
        "schema_version": SCHEMA_VERSION,
        "source_id": SOURCE_ID,
        "compilation_role": ROLE,
        "table_kind": "high_temperature",
        "table_number_as_published": None,
        "pdf_page": pdf_page,
        "page": bulletin_page,
        "name_as_published": name,
        "formula_as_published": formula,
        "formula": formula,
        "phase": " ".join(phase_notes).strip(),
        "gram_formula_weight": gfw,
        "units_as_published": {
            "temperature": "K",
            "H_minus_H298": "kcal gfw^-1",
            "entropy": "cal deg^-1 gfw^-1",
            "gibbs_function": "cal deg^-1 gfw^-1  [printed -(G_T-H_298)/T]",
            "delta_f_H": "kcal gfw^-1",
            "delta_f_G": "kcal gfw^-1",
            "log_Kf": "dimensionless",
        },
        "footer": footer,
        "rows": rows,
        "row_count": len(data_rows),
        "lookup_grid_count": len(lookup_rows),
        "grid_refusals": grid_refusals,
        "ocr_suspect_token_count": ocr_n,
        "identity_disagreements": identity,
        "ambiguities": ambiguities,
    }
    _apply_image_verified_corrections(record)
    return record


def _298k_identity(dG: float | None, logk: float | None) -> str | None:
    if dG is None or logk is None:
        return None
    denom = float(R_CAL) * 298.15 * float(LN10)
    pred = -dG / denom
    delta = abs(pred - logk)
    if delta > 0.08:
        return f"logKf_298_identity |logK+dG_cal/(RT ln10)|={delta:.4f}"
    return None


def _is_298k_main_line(line: str) -> bool:
    if not line or len(line) - len(line.lstrip()) > 2:
        return False
    if re.search(
        r"Name and formula|PROPERTIES AT 298|THERMODYNAMIC PROPERTIES|References|"
        r"Gram\s+formula|Entropy|Std\.|UNCERTAINTY",
        line,
        re.I,
    ):
        return False
    prefix = line[:40]
    spans = _group_split_decimal_spans(line, _token_spans(line))
    if "aqueous ion" in prefix.lower():
        return len([span for span in spans if _is_numberish(span[2])]) >= 2
    return any(
        18 <= start <= 46
        and _is_numberish(token)
        and re.search(r"[A-Za-z]{3,}", line[:start])
        and line[max(0, start - 4) : start].isspace()
        for start, _, token in spans
    )


def _298k_gfw_span(line: str, anchor: int) -> tuple[int, int, str] | None:
    return next(
        (
            span
            for span in _group_split_decimal_spans(line, _token_spans(line))
            if abs(span[0] - anchor) <= 6
            and _is_numberish(span[2])
            and not span[2].startswith("±")
            and line[max(0, span[0] - 2) : span[0]].isspace()
        ),
        None,
    )


def _is_298k_aligned_main(line: str, gfw_anchor: int) -> bool:
    if not _is_298k_main_line(line):
        return False
    if "aqueous ion" in line[:45].lower():
        return True
    return _298k_gfw_span(line, gfw_anchor) is not None


def _298k_layout(lines: list[str]) -> tuple[list[int], list[int], int]:
    reference_positions = []
    for line in lines[:16]:
        match = re.search(r"re[fl].?erenc", line, re.I)
        if match:
            reference_positions.append(match.start())
    reference_start = int(statistics.median(reference_positions)) if reference_positions else 110
    candidates: list[list[tuple[int, int, str]]] = []
    for line in lines:
        if not _is_298k_main_line(line) or "aqueous ion" in line[:40].lower():
            continue
        spans = _group_split_decimal_spans(line, _token_spans(line))
        gfw_index = next(
            (
                index
                for index, (start, _, token) in enumerate(spans)
                if 18 <= start <= 46
                and _is_numberish(token)
                and line[max(0, start - 4) : start].isspace()
            ),
            None,
        )
        if gfw_index is None:
            continue
        value_spans = [
            span
            for span in spans[gfw_index:]
            if span[0] < reference_start and _is_numberish(span[2])
        ]
        starts = [span[0] for span in value_spans]
        if len(starts) >= 6 and all(b - a >= 5 for a, b in zip(starts[:6], starts[1:6])):
            candidates.append(value_spans[:6])
    if not candidates:
        raise ValueError("could not locate the six printed 298.15 K value columns")
    anchors = [int(statistics.median(row[index][0] for row in candidates)) for index in range(6)]
    boundaries = [
        int(statistics.median((row[index][1] + row[index + 1][0]) // 2 for row in candidates))
        for index in range(5)
    ]
    log_end = int(statistics.median(row[5][1] for row in candidates))
    reference_boundary = (log_end + reference_start) // 2
    return anchors, boundaries, reference_boundary


def _phase_and_formula(raw: str, name: str) -> tuple[str, str]:
    raw = raw.strip()
    if "aqueous ion" in name.lower():
        formula = re.sub(r"\s+aqueous ion\s*$", "", name, flags=re.I).strip()
        return formula, "aqueous ion"
    match = re.search(r"\(([^()]*)\)\s*$", raw)
    if match and match.group(1).strip() in PRINTED_PHASE_LABELS:
        phase = match.group(1).strip()
        formula = raw[: match.start()].strip()
    else:
        phase = ""
        formula = raw
    return formula, phase


def _apply_298k_image_verified_identity(record: dict) -> None:
    raw_name = record["name_as_published"]
    correction = IMAGE_VERIFIED_298K_IDENTITIES.get(raw_name)
    if correction is None:
        return
    printed_name, printed_formula, pdf_page = correction
    raw_formula = record["formula_as_published"]
    raw_state_note = record.get("state_note_as_published", "")
    record.update(
        {
            "name_as_published": printed_name,
            "formula_as_published": printed_formula,
            "formula": printed_formula,
            "phase": "aqueous ion",
            "state_note_as_published": "Std. state, m = 1",
        }
    )
    for field, raw, printed in (
        ("name_as_published", raw_name, printed_name),
        ("formula_as_published", raw_formula, printed_formula),
        ("state_note_as_published", raw_state_note, "Std. state, m = 1"),
    ):
        record.setdefault("corrections", []).append(
            {
                "correction_id": f"b1259-p{pdf_page}-{_slug(printed_name, 'ion')}-{field}",
                "pdf_page": pdf_page,
                "field": field,
                "layout_as_extracted": raw,
                "printed_token": printed,
                "evidence": f"PDF page {pdf_page} image visibly prints {printed!r}",
            }
        )


def parse_298k_pages(pages: dict[int, str]) -> list[dict]:
    records: list[dict] = []
    current_group = ""
    seq = 0
    for pdf_page in range(17, 32):
        bulletin_page = pdf_page - 6
        lines = pages[pdf_page].splitlines()
        anchors, boundaries, reference_boundary = _298k_layout(lines)
        i = 0
        while i < len(lines):
            ln = lines[i]
            s = ln.strip()
            i += 1
            if not s:
                continue
            if re.search(r"Name and formula|PROPERTIES AT 298|THERMODYNAMIC PROPERTIES|References|Gram\s+formula|Entropy", s, re.I):
                continue
            if re.match(
                r"^(Elements|Sulfides|Oxides|Ox des|Oxi des|Halides|Carbonates|Nitrates|Sulfates|Phosphates|Ortho|Chain|Frame|Sheet)\b",
                s,
                re.I,
            ) and len(s) < 90:
                current_group = re.sub(r"\s+", " ", s)
                continue
            if s.startswith("Std.") or s.startswith("±") or s.startswith("t "):
                continue
            if not _is_298k_aligned_main(ln, anchors[0]):
                continue
            spans = _group_split_decimal_spans(ln, _token_spans(ln))
            row_boundaries = [_avoid_splitting_token(ln, boundary) for boundary in boundaries]
            row_reference_boundary = _avoid_splitting_token(ln, reference_boundary)
            gfw_span = _298k_gfw_span(ln, anchors[0])
            name_end = gfw_span[0] if gfw_span else row_boundaries[0]
            name = re.sub(r"\s+", " ", ln[:name_end]).strip()
            gfw_raw = ln[gfw_span[0] : row_boundaries[0]].strip() if gfw_span else ""
            value_ranges = [
                (row_boundaries[0], row_boundaries[1]),
                (row_boundaries[1], row_boundaries[2]),
                (row_boundaries[2], row_boundaries[3]),
                (row_boundaries[3], row_boundaries[4]),
                (row_boundaries[4], row_reference_boundary),
            ]
            keys = ["entropy", "molar_volume", "delta_f_H", "delta_f_G", "log_Kf"]
            assigned = {
                key: _cell(ln[start:end].strip())
                for key, (start, end) in zip(keys, value_ranges)
            }
            source_column_spans = {
                key: {"source_span": [start, end], "source_line_index": 0}
                for key, (start, end) in zip(keys, value_ranges)
            }
            if gfw_span:
                gfw = _cell(gfw_raw, "gram_formula_weight_blank_as_published")
                source_column_spans["gram_formula_weight"] = {
                    "source_span": [gfw_span[0], row_boundaries[0]],
                    "source_line_index": 0,
                }
            else:
                gfw = _empty_cell("gram_formula_weight_blank_as_published")
            reference_text = ln[row_reference_boundary:]
            refs = [token for _, _, token in _token_spans(reference_text) if token]

            continuation = ""
            if i < len(lines) and lines[i].strip() and not _is_298k_aligned_main(
                lines[i], anchors[0]
            ):
                continuation = lines[i]
                i += 1
            extra_continuation = ""
            state_note_line = continuation if re.match(r"^\s*Std[.;]?\s+state", continuation, re.I) else ""
            if (
                "aqueous ion" in name.lower()
                and not state_note_line
                and i < len(lines)
                and re.match(r"^\s*Std[.;]?\s+state", lines[i], re.I)
            ):
                extra_continuation = lines[i]
                state_note_line = extra_continuation
                i += 1
            gfw_from_continuation = False
            if not gfw_raw and continuation.strip() and not re.search(r"[A-Za-z]", continuation):
                continuation_spans = _group_split_decimal_spans(
                    continuation, _token_spans(continuation)
                )
                continuation_gfw = next(
                    (
                        span
                        for span in continuation_spans
                        if abs(span[0] - anchors[0]) <= 6 and _is_numberish(span[2])
                    ),
                    None,
                )
                if continuation_gfw:
                    gfw_raw = continuation_gfw[2]
                    gfw = _cell(gfw_raw)
                    source_column_spans["gram_formula_weight"] = {
                        "source_span": [continuation_gfw[0], continuation_gfw[1]],
                        "source_line_index": 1,
                    }
                    gfw_from_continuation = True
            formula_region = continuation[: boundaries[0]].strip()
            if gfw_from_continuation:
                formula_region = ""
            formula_region = re.split(r"\s{2,}(?=[±t])", formula_region, maxsplit=1)[0].strip()
            formula, phase = _phase_and_formula(formula_region, name)
            uncertainty_line = state_note_line or continuation
            unc_tokens = [
                token
                for _, _, token in _token_spans(uncertainty_line)
                if token.startswith("±") or token == "±" or _is_numberish(token)
            ]
            refs.extend(token for _, _, token in _token_spans(continuation[reference_boundary:]))
            if extra_continuation:
                refs.extend(
                    token
                    for _, _, token in _token_spans(extra_continuation[reference_boundary:])
                )
            note = _298k_identity(assigned["delta_f_G"]["value"], assigned["log_Kf"]["value"])
            ambiguities = []
            if note:
                ambiguities.append(note)
            if any(assigned[k]["ocr_suspect"] for k in keys) or gfw["ocr_suspect"]:
                ambiguities.append("ocr_suspect_token_retained_uncorrected")
            seq += 1
            record = {
                    "schema_version": SCHEMA_VERSION,
                    "source_id": SOURCE_ID,
                    "compilation_role": ROLE,
                    "table_kind": "properties_298k",
                    "table_number_as_published": None,
                    "group_as_published": current_group,
                    "pdf_page": pdf_page,
                    "page": bulletin_page,
                    "name_as_published": name,
                    "formula_as_published": formula,
                    "formula": formula,
                    "phase": phase,
                    "state_note_as_published": (
                        state_note_line[: boundaries[0]].strip() if state_note_line else ""
                    ),
                    "gram_formula_weight": gfw,
                    "units_as_published": {
                        "entropy": "cal deg^-1 gfw^-1",
                        "molar_volume": "cm^3",
                        "delta_f_H": "cal gfw^-1",
                        "delta_f_G": "cal gfw^-1",
                        "log_Kf": "dimensionless",
                    },
                    "entropy": assigned["entropy"],
                    "molar_volume": assigned["molar_volume"],
                    "delta_f_H": assigned["delta_f_H"],
                    "delta_f_G": assigned["delta_f_G"],
                    "log_Kf": assigned["log_Kf"],
                    "references_as_published": refs,
                    "uncertainty_line_tokens": unc_tokens,
                    "source_layout_lines": [
                        line for line in (ln, continuation, extra_continuation) if line
                    ],
                    "source_column_spans": source_column_spans,
                    "row_count": 1,
                    "identity_disagreements": [note] if note else [],
                    "ambiguities": ambiguities,
                    "_seq": seq,
                }
            _apply_298k_image_verified_identity(record)
            records.append(record)
    return records


def parse_table1(text: str) -> dict:
    rows = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("TABLE 1") or "PHYSICAL CONSTANTS" in s or "THERMODYNAMIC" in s:
            continue
        m = re.match(r"^(\S+)\s{2,}(.+)$", ln.strip())
        if not m:
            m = re.match(r"^(\S+)\s+(.+)$", s)
        if not m:
            continue
        symbol, definition = m.group(1), m.group(2).strip()
        if symbol.lower() in {"the", "values", "for", "of"}:
            continue
        numbers = [inspect_token(t) for t in re.findall(r"[+-]?(?:\d+\.\d+|\d+)", definition)]
        rows.append(
            {
                "symbol_as_published": symbol,
                "definition_as_published": definition,
                "numeric_tokens": numbers,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "source_id": SOURCE_ID,
        "compilation_role": ROLE,
        "table_kind": "symbols_and_constants",
        "table_number_as_published": "1",
        "pdf_page": 9,
        "page": 3,
        "name_as_published": "Symbols and constants",
        "formula_as_published": "",
        "formula": "",
        "phase": "",
        "rows": rows,
        "row_count": len(rows),
        "units_as_published": "as printed in table 1",
        "identity_disagreements": [],
        "ambiguities": ["OCR of subscripts and unit exponents retained as printed"],
    }


def parse_table2(text: str) -> dict:
    rows = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("TABLE 2") or s.startswith("Atomic") or s.startswith("Element"):
            continue
        if "THERMODYNAMIC" in s:
            continue
        tokens = s.split()
        if len(tokens) < 2:
            continue
        # Two side-by-side entries; split at a second capitalised element name if present.
        entries = [tokens]
        # keep whole line as one or two records by finding a second Element-like token after a weight
        nums = [(i, t) for i, t in enumerate(tokens) if _is_numberish(t)]
        if not nums:
            rows.append(
                {
                    "line_as_published": s,
                    "element_as_published": tokens[0],
                    "symbol_as_published": tokens[1] if len(tokens) > 1 else "",
                    "weight": _empty_cell("weight_absent_or_unreadable"),
                    "ocr_suspect": True,
                }
            )
            continue
        # left entry: tokens before midpoint if two weights
        if len(nums) >= 2:
            mid = nums[0][0] + 1
            left, right = tokens[:mid], tokens[mid:]
            for part in (left, right):
                if not part:
                    continue
                ntoks = [t for t in part if _is_numberish(t)]
                words = [t for t in part if not _is_numberish(t)]
                weight = inspect_token(ntoks[-1]) if ntoks else _empty_cell("weight_absent_or_unreadable")
                symbol = ""
                element = " ".join(words)
                for w in words:
                    if re.fullmatch(r"[A-Z][a-z]?[0-9]*", w) or re.fullmatch(r"[A-Za-z]{1,3}\d{0,3}", w):
                        symbol = w
                rows.append(
                    {
                        "line_as_published": s,
                        "element_as_published": element,
                        "symbol_as_published": symbol,
                        "weight": weight,
                        "ocr_suspect": weight["ocr_suspect"] or not ntoks,
                    }
                )
        else:
            ntoks = [t for t in tokens if _is_numberish(t)]
            words = [t for t in tokens if not _is_numberish(t)]
            weight = inspect_token(ntoks[-1])
            symbol = words[-1] if words else ""
            rows.append(
                {
                    "line_as_published": s,
                    "element_as_published": " ".join(words[:-1]) if len(words) > 1 else " ".join(words),
                    "symbol_as_published": symbol,
                    "weight": weight,
                    "ocr_suspect": weight["ocr_suspect"],
                }
            )
    ambiguities = []
    n_sus = sum(1 for r in rows if r.get("ocr_suspect"))
    if n_sus:
        ambiguities.append(f"{n_sus} atomic-weight tokens ocr_suspect; raw tokens retained uncorrected")
    return {
        "schema_version": SCHEMA_VERSION,
        "source_id": SOURCE_ID,
        "compilation_role": ROLE,
        "table_kind": "atomic_weights_1963",
        "table_number_as_published": "2",
        "pdf_page": 10,
        "page": 4,
        "name_as_published": "Atomic weights for 1963",
        "formula_as_published": "",
        "formula": "",
        "phase": "",
        "rows": rows,
        "row_count": len(rows),
        "units_as_published": "scale C12 = 12.0000 as printed",
        "identity_disagreements": [],
        "ambiguities": ambiguities,
    }


def parse_table3(text: str) -> dict:
    rows = []
    buf_author = []
    buf_data = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("TABLE 3") or s.startswith("[Dates") or "THERMODYNAMIC" in s:
            continue
        if "Authors and date" in s or "Type of data" in s:
            continue
        # continuation lines are indented in source; keep as printed
        rows.append({"line_as_published": s})
    return {
        "schema_version": SCHEMA_VERSION,
        "source_id": SOURCE_ID,
        "compilation_role": ROLE,
        "table_kind": "critical_summaries_bibliography",
        "table_number_as_published": "3",
        "pdf_page": 12,
        "page": 6,
        "name_as_published": "Chronological list of important critical summaries of thermodynamic data for inorganic substances",
        "formula_as_published": "",
        "formula": "",
        "phase": "",
        "rows": rows,
        "row_count": len(rows),
        "units_as_published": "",
        "identity_disagreements": [],
        "ambiguities": ["bibliographic table; no thermodynamic numbers to parse"],
    }


def lookup(record: dict, temperature: float) -> list[dict]:
    """Return every printed row at this T. Refuse if T is not on the printed grid."""
    if record.get("untranscribed"):
        raise TemperatureNotOnPrintedGrid(
            f"{record.get('record_id')}: table untranscribed; no T grid"
        )
    if record.get("table_kind") != "high_temperature":
        raise TemperatureNotOnPrintedGrid(
            f"{record.get('record_id')}: lookup is defined only on high-temperature T grids"
        )
    want = Decimal(str(temperature))
    hits = []
    for row in record.get("rows") or []:
        if row.get("kind") != "data":
            continue
        cell = row.get("temperature") or {}
        if cell.get("value") is None:
            continue
        printed = Decimal(str(cell["value"]))
        if printed == want:
            hits.append(row)
    if not hits:
        refusals = record.get("grid_refusals") or []
        if refusals:
            details = "; ".join(
                f"raw={item.get('raw_temperature_token')!r}: {item.get('reason')}"
                for item in refusals[:3]
            )
            if len(refusals) > 3:
                details += f"; and {len(refusals) - 3} more"
            raise TemperatureNotOnPrintedGrid(
                f"{record.get('record_id')}: T={temperature} cannot be admitted from the "
                f"printed grid because source temperature tokens are ambiguous ({details})"
            )
        raise TemperatureNotOnPrintedGrid(
            f"{record.get('record_id')}: T={temperature} is not a printed grid point "
            "(no interpolation, no extrapolation, no zero default)"
        )
    return hits


def load_manifest(root: Path = COMPILATION_ROOT) -> dict:
    return yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))


def load_records(root: Path = COMPILATION_ROOT):
    manifest = load_manifest(root)
    for entry in manifest["entries"]:
        path = root / entry["path"] if not Path(entry["path"]).is_absolute() else Path(entry["path"])
        record = json.loads(path.read_text(encoding="utf-8"))
        if record["record_id"] != entry["record_id"]:
            raise ValueError(f"record_id mismatch: {entry['path']}")
        if record["compilation_role"]["battery_refusal"] != ROLE["battery_refusal"]:
            raise ValueError(f"compilation_role mismatch: {entry['path']}")
        yield record


def validate_record_source_tokens(record: dict, page_text: str) -> int:
    """Verify every stored raw numeric token at its exact source layout column."""
    def check_cell(cell: dict, source_lines: list[str], locator: object) -> int:
        raw = cell.get("layout_as_extracted", cell.get("as_published", ""))
        if not raw:
            return 0
        if isinstance(locator, list):
            span = locator
            line_index = 0
        elif isinstance(locator, dict):
            span = locator.get("source_span")
            line_index = locator.get("source_line_index", 0)
        else:
            span = None
            line_index = 0
        if (
            not isinstance(span, list)
            or len(span) != 2
            or not all(isinstance(value, int) for value in span)
            or not isinstance(line_index, int)
            or not 0 <= line_index < len(source_lines)
        ):
            raise ValueError(
                f"{record.get('record_id')}: raw token {raw!r} has no source column locator "
                f"for PDF page {record.get('pdf_page')}"
            )
        observed = source_lines[line_index][span[0] : span[1]].strip()
        if observed != raw:
            raise ValueError(
                f"{record.get('record_id')}: raw token {raw!r} does not match PDF page "
                f"{record.get('pdf_page')} layout column token {observed!r}"
            )
        return 1

    checked = 0
    if record.get("table_kind") == "properties_298k":
        source_lines = record.get("source_layout_lines") or []
        if not source_lines or any(line not in page_text for line in source_lines):
            raise ValueError(f"{record.get('record_id')}: source layout line missing from page")
        page_lines = page_text.splitlines()
        anchors, boundaries, reference_boundary = _298k_layout(page_lines)
        main = source_lines[0]
        row_boundaries = [_avoid_splitting_token(main, boundary) for boundary in boundaries]
        row_reference_boundary = _avoid_splitting_token(main, reference_boundary)
        value_ranges = [
            (row_boundaries[0], row_boundaries[1]),
            (row_boundaries[1], row_boundaries[2]),
            (row_boundaries[2], row_boundaries[3]),
            (row_boundaries[3], row_boundaries[4]),
            (row_boundaries[4], row_reference_boundary),
        ]
        gfw_span = _298k_gfw_span(main, anchors[0])
        derived_locators: dict[str, object] = {
            field: {"source_span": list(span), "source_line_index": 0}
            for field, span in zip(
                ("entropy", "molar_volume", "delta_f_H", "delta_f_G", "log_Kf"),
                value_ranges,
            )
        }
        if gfw_span:
            derived_locators["gram_formula_weight"] = {
                "source_span": [gfw_span[0], row_boundaries[0]],
                "source_line_index": 0,
            }
        elif len(source_lines) > 1:
            continuation_spans = _group_split_decimal_spans(
                source_lines[1], _token_spans(source_lines[1])
            )
            continuation_gfw = next(
                (
                    span
                    for span in continuation_spans
                    if abs(span[0] - anchors[0]) <= 6 and _is_numberish(span[2])
                ),
                None,
            )
            if continuation_gfw:
                derived_locators["gram_formula_weight"] = {
                    "source_span": [continuation_gfw[0], continuation_gfw[1]],
                    "source_line_index": 1,
                }
        cells = [record.get("gram_formula_weight") or {}]
        cells.extend(record.get(key) or {} for key in ("entropy", "molar_volume", "delta_f_H", "delta_f_G", "log_Kf"))
        fields = ("gram_formula_weight", "entropy", "molar_volume", "delta_f_H", "delta_f_G", "log_Kf")
        for field, cell in zip(fields, cells):
            checked += check_cell(cell, source_lines, derived_locators.get(field))
    elif record.get("table_kind") == "high_temperature":
        if not record.get("rows"):
            return checked
        column_ranges = _high_t_column_ranges(page_text.splitlines())
        for row in record.get("rows") or []:
            line = row.get("source_layout_line")
            if not line:
                continue
            if line not in page_text:
                raise ValueError(f"{record.get('record_id')}: source layout row missing from page")
            spans = _group_split_decimal_spans(line, _token_spans(line))
            numeric_spans = [span for span in spans if _is_numberish(span[2])]
            for column in HIGH_T_COLUMNS:
                if column == "temperature" and numeric_spans and numeric_spans[0][0] <= 8:
                    start, end, _ = numeric_spans[0]
                elif (
                    column == "H_minus_H298"
                    and len(numeric_spans) > 1
                    and numeric_spans[0][0] <= 8
                    and inspect_token(numeric_spans[0][2]).get("value") == 298.15
                    and numeric_spans[1][0] <= 12
                ):
                    start, end, _ = numeric_spans[1]
                else:
                    start, end = column_ranges[column]
                checked += check_cell(
                    row.get(column) or {},
                    [line],
                    [start, len(line) if end is None else end],
                )
    return checked


def _slug(text: str, fallback: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return (s[:40] or fallback)


def _source_block() -> dict:
    return {
        "database": "USGS Bulletin 1259, Robie & Waldbaum 1968",
        "citation": (
            "Robie, R. A. and Waldbaum, D. R., 1968, Thermodynamic properties of minerals "
            "and related substances at 298.15 K (25.0 C) and one atmosphere (1.013 bars) "
            "pressure and at higher temperatures: U.S. Geological Survey Bulletin 1259, 256 p."
        ),
        "doi": "10.3133/b1259",
        "official_url": PDF_URL,
        "licence": (
            "United States government work. USGS numbered series; public domain in the "
            "United States. Fetched from the official pubs.usgs.gov host."
        ),
        "sha256": PDF_SHA256,
        "retrieved_at": "2026-09-06",
    }


def _write_json(path: Path, obj: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    path.write_text(payload, encoding="utf-8")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _pdf_paths() -> list[Path]:
    return [
        Path("/Users/simonrowland/Repos/regolith-corpus/raw/robie-waldbaum-1968-usgs-b1259/robie-waldbaum-1968-usgs-b1259.pdf"),
        Path("/Users/simonrowland/Repos/regolith-corpus-ctl/raw/robie-waldbaum-1968-usgs-b1259/robie-waldbaum-1968-usgs-b1259.pdf"),
    ]


def ensure_page_texts(page_dir: Path) -> Path:
    page_dir.mkdir(parents=True, exist_ok=True)
    if all((page_dir / f"page-{index:03d}.txt").is_file() for index in range(1, 263)):
        return page_dir
    pdf = next((p for p in _pdf_paths() if p.is_file()), None)
    if pdf is None:
        raise FileNotFoundError("USGS B1259 PDF not found in corpus raw/")
    for i in range(1, 263):
        out = page_dir / f"page-{i:03d}.txt"
        subprocess.run(
            ["pdftotext", "-layout", "-f", str(i), "-l", str(i), str(pdf), str(out)],
            check=True,
        )
    return page_dir


def harvest(page_dir: Path | None = None, output: Path = COMPILATION_ROOT) -> dict:
    page_dir = ensure_page_texts(page_dir or Path("/tmp/robie-waldbaum-1968-usgs-b1259-pages"))
    pages = {
        i: (page_dir / f"page-{i:03d}.txt").read_text(encoding="utf-8", errors="replace")
        for i in range(1, 263)
        if (page_dir / f"page-{i:03d}.txt").is_file()
    }
    layout_text = "\f".join(pages[index].rstrip("\f") for index in range(1, 263)) + "\f"
    layout_path = output / "source/layout.txt"
    layout_path.parent.mkdir(parents=True, exist_ok=True)
    layout_path.write_text(layout_text, encoding="utf-8")
    layout_sha256 = hashlib.sha256(layout_text.encode("utf-8")).hexdigest()
    records_dir = output / "records"
    if records_dir.exists():
        for old in records_dir.glob("*.json"):
            old.unlink()
    records_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    source = _source_block()
    untranscribed = []
    identity_all = []
    ocr_examples = []
    corrections_all = []

    def add_entry(record: dict, filename: str) -> None:
        corrections_all.extend(record.get("corrections") or [])
        record["source"] = source
        path = records_dir / filename
        digest = _write_json(path, record)
        rel = f"records/{filename}"
        entries.append(
            {
                "record_id": record["record_id"],
                "path": rel,
                "formula": record.get("formula_as_published") or record.get("formula") or "",
                "phase": record.get("phase") or "",
                "name_as_published": record.get("name_as_published") or "",
                "source": source,
                "table_kind": record.get("table_kind"),
                "page": record.get("page"),
                "pdf_page": record.get("pdf_page"),
                "table_number_as_published": record.get("table_number_as_published"),
                "row_count": record.get("row_count", 0),
                "ambiguity_count": len(record.get("ambiguities") or []),
                "ambiguities": record.get("ambiguities") or [],
                "untranscribed": bool(record.get("untranscribed")),
                "sha256": PDF_SHA256,
                "record_sha256": digest,
            }
        )

    t1 = parse_table1(pages[9])
    t1["record_id"] = "b1259-table-01-symbols"
    add_entry(t1, "b1259-table-01-symbols.json")
    print("PROGRESS table 1 symbols page=3 rows=%s" % t1["row_count"])

    t2 = parse_table2(pages[10])
    t2["record_id"] = "b1259-table-02-atomic-weights"
    add_entry(t2, "b1259-table-02-atomic-weights.json")
    print("PROGRESS table 2 atomic-weights page=4 rows=%s" % t2["row_count"])

    t3 = parse_table3(pages[12])
    t3["record_id"] = "b1259-table-03-critical-summaries"
    add_entry(t3, "b1259-table-03-critical-summaries.json")
    print("PROGRESS table 3 critical-summaries page=6 rows=%s" % t3["row_count"])

    recs_298 = parse_298k_pages(pages)
    actual_298k_page_counts = tuple(
        sum(record["pdf_page"] == pdf_page for record in recs_298)
        for pdf_page in range(17, 32)
    )
    if actual_298k_page_counts != EXPECTED_298K_PAGE_COUNTS:
        raise ValueError(
            f"298.15 K source census mismatch: {actual_298k_page_counts} != {EXPECTED_298K_PAGE_COUNTS}"
        )
    for rec in recs_298:
        seq = rec.pop("_seq")
        rec["record_id"] = f"b1259-298k-{seq:04d}-{_slug(rec['name_as_published'], 'substance')}"
        add_entry(rec, f"{rec['record_id']}.json")
        print(
            "PROGRESS table 298k %s page=%s name=%r rows=1"
            % (rec["record_id"], rec["page"], rec["name_as_published"])
        )
        identity_all.extend(rec.get("identity_disagreements") or [])

    ht_index = 0
    for pdf_page in range(32, 244):
        ht_index += 1
        if pdf_page == 125:
            rec = {
                "schema_version": SCHEMA_VERSION,
                "source_id": SOURCE_ID,
                "compilation_role": ROLE,
                "record_id": "b1259-ht-0094-untranscribed",
                "table_kind": "high_temperature",
                "table_number_as_published": None,
                "pdf_page": 125,
                "page": 119,
                "name_as_published": "",
                "formula_as_published": "",
                "formula": "",
                "phase": "",
                "untranscribed": True,
                "untranscribed_reason": (
                    "PDF page 125 (bulletin p. 119) has page rotation 180°; the OCR text "
                    "layer is inverted glyphs and cannot be recovered without guessing. "
                    "Neighbors are Li2O (p. 118) and brucite (p. 120)."
                ),
                "page_range": {"pdf": [125, 125], "bulletin": [119, 119]},
                "rows": [],
                "row_count": 0,
                "identity_disagreements": [],
                "ambiguities": ["untranscribed_rotated_ocr"],
            }
            untranscribed.append(rec["untranscribed_reason"])
            add_entry(rec, f"{rec['record_id']}.json")
            print("PROGRESS table highT pdf=125 bull=119 UNTRANSCRIBED rotated OCR")
            continue
        rec = parse_high_t_page(pdf_page, pages[pdf_page])
        slug = _slug(rec["name_as_published"] or rec["formula_as_published"] or f"p{pdf_page}", f"p{pdf_page}")
        rec["record_id"] = f"b1259-ht-{ht_index:04d}-{slug}"
        if rec["row_count"] == 0:
            rec["untranscribed"] = True
            rec["untranscribed_reason"] = (
                "no recoverable T-grid rows from the OCR text layer; not guessed"
            )
            rec.setdefault("ambiguities", []).append("untranscribed_empty_t_grid")
            untranscribed.append(f"pdf {pdf_page} bulletin {rec['page']}: empty T grid")
        add_entry(rec, f"{rec['record_id']}.json")
        identity_all.extend(rec.get("identity_disagreements") or [])
        for row in rec.get("rows") or []:
            if row.get("kind") != "data":
                continue
            for col in HIGH_T_COLUMNS:
                cell = row.get(col) or {}
                if cell.get("ocr_suspect") and cell.get("as_published") and len(ocr_examples) < 12:
                    ocr_examples.append(
                        {
                            "record_id": rec["record_id"],
                            "page": rec["page"],
                            "column": col,
                            "as_published": cell["as_published"],
                        }
                    )
        print(
            "PROGRESS table highT pdf=%s bull=%s name=%r formula=%r rows=%s ocr=%s id=%s"
            % (
                pdf_page,
                rec["page"],
                rec["name_as_published"],
                rec["formula_as_published"],
                rec["row_count"],
                rec.get("ocr_suspect_token_count"),
                len(rec.get("identity_disagreements") or []),
            )
        )

    n_un = sum(1 for e in entries if e.get("untranscribed"))
    n_tr = len(entries) - n_un
    census = {
        "toc_named_tables": 3,
        "properties_298k_substances": len(recs_298),
        "high_temperature_substance_tables": 212,
        "total": len(entries),
        "transcribed": n_tr,
        "untranscribed_count": n_un,
        "untranscribed": [
            {
                "record_id": e["record_id"],
                "pdf_page": e.get("pdf_page"),
                "bulletin_page": e.get("page"),
                "reason": next(
                    (
                        json.loads((output / e["path"]).read_text(encoding="utf-8")).get(
                            "untranscribed_reason", "untranscribed"
                        )
                        for _ in [0]
                    ),
                    "untranscribed",
                ),
            }
            for e in entries
            if e.get("untranscribed")
        ],
        "page_ranges": {
            "table_1_symbols": {"pdf": [9, 9], "bulletin": [3, 3]},
            "table_2_atomic_weights": {"pdf": [10, 10], "bulletin": [4, 4]},
            "table_3_critical_summaries": {"pdf": [12, 12], "bulletin": [6, 6]},
            "properties_298k": {"pdf": [17, 31], "bulletin": [11, 25]},
            "high_temperature": {"pdf": [32, 243], "bulletin": [26, 237]},
        },
    }
    manifest = {
        "schema_version": "literature_compilation_manifest.v1",
        "source_id": SOURCE_ID,
        "compilation_role": ROLE,
        "source": source,
        "source_files": [
            {
                "path": "corpus PDF (not copied into this repository)",
                "sha256": PDF_SHA256,
                "official_url": PDF_URL,
            },
            {
                "path": "source/layout.txt",
                "sha256": layout_sha256,
                "generated_by": "pdftotext -layout, one form-feed-delimited page per PDF page",
            },
        ],
        "corpus_status": {
            "scope": "every numeric table in USGS Bulletin 1259",
            "abstract_claimed_298k": "50 reference elements and 285 minerals and related substances",
            "abstract_claimed_highT": 211,
            "ambiguities": untranscribed + [
                "298.15 K section OCR is ragged; tokens retained uncorrected",
                "high-T computer printout OCR confuses O/0, C/0, l/1; tokens retained uncorrected",
                "thermodynamic identities used only as detectors",
            ],
        },
        "census": census,
        "summary": {
            "record_count": len(entries),
            "transcribed_count": n_tr,
            "untranscribed_count": n_un,
            "record_ambiguity_count": sum(e["ambiguity_count"] for e in entries),
            "identity_disagreement_count": len(identity_all),
            "ocr_suspect_examples": ocr_examples,
        },
        "corrections": corrections_all,
        "entries": entries,
    }
    (output / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    harvest()


if __name__ == "__main__":
    main()
