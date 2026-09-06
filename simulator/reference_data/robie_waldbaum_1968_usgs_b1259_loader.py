"""Robie & Waldbaum 1968 (USGS Bulletin 1259) compilation loader.

Native transcription of the printed tables. No unit conversion, no rounding,
no interpolation, no gap filling. OCR tokens are kept as printed; identity
relations are detectors only.
"""

from __future__ import annotations

import hashlib
import json
import re
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


def _merge_split_decimals(tokens: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if nxt is not None:
            if tok.endswith(".") and re.fullmatch(r"\d+[A-Za-z]?", nxt):
                out.append(tok + nxt)
                i += 2
                continue
            if (
                _is_numberish(tok)
                and "." not in tok
                and nxt.startswith(".")
                and _is_numberish(nxt)
            ):
                out.append(tok + nxt)
                i += 2
                continue
        out.append(tok)
        i += 1
    return out


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


def _clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip(" .")
    name = re.sub(r"^\d+\s+", "", name)
    name = re.sub(r"^(?:THERMO\s*)?DYNAMIC PROPERTIES OF MINERALS\s*", "", name, flags=re.I)
    name = re.sub(r"^PROPERTIES AT HIGH TEMPERATURES\s*\d*\s*", "", name, flags=re.I)
    return name.strip(" .")


def _parse_high_t_rows(lines: list[str]) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    identity: list[str] = []
    for ln in lines:
        if re.match(r"^\s*(?:MELTING|BOILING)\s+POINT\b", ln, re.I) and re.search(
            r"(?:DEG|OEG|DEC|PEG)\s*K", ln, re.I
        ):
            break
        if re.match(r"^\s*HEAT OF (?:FUSION|VAPOR)", ln, re.I):
            break
        if re.match(r"^\s*(?:REFERENCES?|RFFERENCCS|COMPILED|TRANSITIONS IN REFERENCE)\b", ln, re.I):
            break
        tokens = _merge_split_decimals(ln.split())
        if not tokens:
            continue
        if tokens[0].upper().startswith("UNCERT"):
            nums = [t for t in tokens[1:] if _is_numberish(t)]
            fields = ("entropy", "gibbs_function", "delta_f_H", "delta_f_G", "log_Kf")
            row = {"kind": "uncertainty"}
            for field, tok in zip(fields, nums):
                row[field] = inspect_token(tok)
            rows.append(row)
            continue
        nums = [t for t in tokens if _is_numberish(t)]
        if len(nums) < 4:
            continue
        t0 = nums[0]
        t_cell = inspect_token(t0)
        T = t_cell["value"]
        if T is None:
            continue
        if T < 50 or T > 2500:
            continue
        if T < 250 and "." not in t0 and abs(T - 298.15) > 1:
            continue
        row = {"kind": "data", "column_token_count": len(nums)}
        for col, tok in zip(HIGH_T_COLUMNS, nums):
            row[col] = inspect_token(tok)
        if len(nums) < 7:
            row["ocr_suspect_row"] = True
            row["missing_columns"] = list(HIGH_T_COLUMNS[len(nums) :])
            for col in HIGH_T_COLUMNS[len(nums) :]:
                row[col] = _empty_cell("column_absent_in_ocr")
        if len(nums) > 7:
            row["ocr_suspect_row"] = True
            row["extra_tokens"] = nums[7:]
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
        elif after_header or gfw:
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
    data_rows = [r for r in rows if r.get("kind") == "data"]
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
    return {
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
        "ocr_suspect_token_count": ocr_n,
        "identity_disagreements": identity,
        "ambiguities": ambiguities,
    }


def _298k_identity(dG: float | None, logk: float | None) -> str | None:
    if dG is None or logk is None:
        return None
    denom = float(R_CAL) * 298.15 * float(LN10)
    pred = -dG / denom
    delta = abs(pred - logk)
    if delta > 0.08:
        return f"logKf_298_identity |logK+dG_cal/(RT ln10)|={delta:.4f}"
    return None


def parse_298k_pages(pages: dict[int, str]) -> list[dict]:
    records: list[dict] = []
    current_group = ""
    seq = 0
    for pdf_page in range(17, 32):
        bulletin_page = pdf_page - 6
        lines = pages[pdf_page].splitlines()
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
            tokens = _merge_split_decimals(s.split())
            num_idx = [j for j, t in enumerate(tokens) if _is_numberish(t)]
            if len(num_idx) < 2:
                continue
            name_tokens = tokens[: num_idx[0]]
            if not name_tokens:
                continue
            if name_tokens[0] in {"Ag", "Al", "As", "Au", "B", "Ba", "Be", "Bi", "C", "Ca"} and len(name_tokens) == 1:
                # formula-only continuation
                continue
            name = " ".join(name_tokens)
            if re.match(
                r"^(Gram|weight|rormula|formula|Entropy|Name|Log|References|Std\.|"
                r"[</tT]\\|os|I x|Kf\.|AH°)",
                name,
                re.I,
            ):
                continue
            if not re.search(r"[A-Za-z]{3,}", name) and "aqueous" not in name.lower():
                continue
            nums = [tokens[j] for j in num_idx]
            formula = ""
            unc_tokens: list[str] = []
            if i < len(lines):
                nxt = lines[i].strip()
                ntoks = nxt.split()
                if ntoks and (nxt.startswith("±") or "±" in nxt or ntoks[0][0].isalpha() or ntoks[0][0] in "A"):
                    # formula / uncertainty line
                    formula_parts = []
                    for t in ntoks:
                        if t.startswith("±") or t in {"±"} or _is_numberish(t) or t in {".", "t"}:
                            unc_tokens.append(t)
                        else:
                            if not unc_tokens:
                                formula_parts.append(t)
                    formula = " ".join(formula_parts)
                    i += 1
            cells = [inspect_token(t) for t in nums]
            gfw = cells[0] if cells else _empty_cell("missing")
            rest = cells[1:]
            assigned = {
                "entropy": _empty_cell("blank_as_published"),
                "molar_volume": _empty_cell("blank_as_published"),
                "delta_f_H": _empty_cell("blank_as_published"),
                "delta_f_G": _empty_cell("blank_as_published"),
                "log_Kf": _empty_cell("blank_as_published"),
            }
            refs: list[str] = []
            # Gases have V ~ 24465; aqueous ions often skip S, V, dH.
            values_only = []
            ref_only = []
            for cell in rest:
                v = cell["value"]
                if v is not None and (v > 250 or (v > 40 and cell["as_published"].isdigit())):
                    # likely a reference number if integer 1-250 and trailing
                    pass
                if (
                    v is not None
                    and v == int(v)
                    and 1 <= v <= 250
                    and "." not in cell["as_published"]
                ):
                    ref_only.append(cell["as_published"])
                else:
                    values_only.append(cell)
            refs = ref_only
            keys = ["entropy", "molar_volume", "delta_f_H", "delta_f_G", "log_Kf"]
            if "aqueous" in name.lower():
                # typically gfw, dG, logK
                if len(values_only) >= 2:
                    assigned["delta_f_G"] = values_only[-2] if len(values_only) >= 2 else values_only[0]
                    assigned["log_Kf"] = values_only[-1]
                    if len(values_only) >= 3:
                        assigned["delta_f_H"] = values_only[-3]
            else:
                for key, cell in zip(keys, values_only):
                    assigned[key] = cell
            note = _298k_identity(assigned["delta_f_G"]["value"], assigned["log_Kf"]["value"])
            ambiguities = []
            if note:
                ambiguities.append(note)
            if any(assigned[k]["ocr_suspect"] for k in keys) or gfw["ocr_suspect"]:
                ambiguities.append("ocr_suspect_token_retained_uncorrected")
            seq += 1
            records.append(
                {
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
                    "phase": name,
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
                    "row_count": 1,
                    "identity_disagreements": [note] if note else [],
                    "ambiguities": ambiguities,
                    "_seq": seq,
                }
            )
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
    sample = page_dir / "page-032.txt"
    if sample.is_file():
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

    def add_entry(record: dict, filename: str) -> None:
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
        "high_temperature_substance_tables": 211,
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
            }
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
