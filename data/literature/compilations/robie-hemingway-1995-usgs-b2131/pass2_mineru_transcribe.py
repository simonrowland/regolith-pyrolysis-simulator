"""Fill the 111 untranscribed B2131 records from MinerU tables + page images.

Does not rewrite transcribed records. MinerU cells are the printed tokens when
they match the page image; disagreements keep both raw tokens and stay suspect.
Page 49 MinerU merged name/formula/T-range rows; those seven records are taken
from the rendered page image, with MinerU coefficients as the second reading.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from html.parser import HTMLParser
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent
MINERU = Path("/Users/simonrowland/Repos/regolith-corpus/text/robie-hemingway-1995-usgs-b2131/mineru")
TESS = Path("/private/tmp/b2131-pass2-pages")
NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?\Z")
ROLE = {
    "engine_reference_input": True,
    "validation_measurement": False,
    "scoring_eligible": False,
    "battery_refusal": "gibbs_table_not_runtime_observable",
}
CP_COLUMNS = (
    "entropy", "A1", "A2", "A3", "A4", "A5",
    "temperature_range", "transition_temperature", "transition_enthalpy",
)
REF_COLUMNS = (
    "weight", "entropy", "volume", "formation_enthalpy", "formation_gibbs", "log_kf",
    "reference_entropy", "reference_enthalpy_gibbs", "reference_heat_capacity",
)
HT_COLUMNS = (
    "temperature", "cp", "entropy", "enthalpy_function", "gibbs_function",
    "formation_enthalpy", "formation_gibbs", "log_kf",
)

# Printed page 49 (PDF 55): MinerU concatenated the two substance lines.
# Values below are the page-image tokens; A-coefficients agree with MinerU.
PAGE49 = {
    "cp-p049-02": {
        "name": "HAUSMANNITE", "formula": "Mn3O4", "phase": [],
        "lines": [
            ("HAUSMANNITE", ["164.1", "-7.432E+00", "9.487E-02", "-6.712E+06", "3.396E+03", "", "298", "", ""]),
            ("Mn3O4", ["0.3", "", "", "", "", "", "1400", "", ""]),
        ],
    },
    "cp-p049-04": {
        "name": "MOLYBDITE", "formula": "MoO3", "phase": [],
        "lines": [
            ("MOLYBDITE", ["77.7", "6.433E+00", "6.278E-02", "-2.460E+06", "1.337E+03", "", "298", "1074", ""]),
            ("MoO3", ["0.4", "", "", "", "", "", "1074", "", ""]),
        ],
    },
    "cp-p049-05": {
        "name": "NITROGEN DIOXIDE", "formula": "NO2", "phase": [],
        "lines": [
            ("NITROGEN DIOXIDE", ["240.1", "1.018E+02", "-1.121E-02", "1.218E+06", "-1.300E+03", "1.461E-06", "298", "", ""]),
            ("NO2", ["0.1", "", "", "", "", "", "2500", "", ""]),
        ],
    },
    "cp-p049-07": {
        "name": "BUNSENITE", "formula": "NiO", "phase": [],
        "lines": [
            ("BUNSENITE", ["38.0", "4.1107E+03", "-5.3024E+00", "2.43067E+07", "-5.3039E+04", "3.5206E-03", "298", "", ""]),
            ("NiO", ["0.2", "", "", "", "", "", "519", "", ""]),
        ],
    },
    "cp-p049-08": {
        "name": "BUNSENITE", "formula": "NiO", "phase": [],
        "lines": [
            ("BUNSENITE", ["67.7", "-8.776E+00", "4.223E-02", "3.607E+06", "7.873E+02", "-7.526E-06", "519", "", ""]),
            ("NiO", ["0.5", "", "", "", "", "", "1800", "", ""]),
        ],
    },
    "cp-p049-12": {
        "name": "SULFUR DIOXIDE", "formula": "SO2", "phase": ["(IDEAL GAS)"],
        "lines": [
            ("SULFUR DIOXIDE", ["248.2", "9.890E+01", "-1.161E-02", "8.465E+05", "-1.127E+03", "1.788E-06", "298", "", ""]),
            ("SO2 (IDEAL GAS)", ["0.1", "", "", "", "", "", "2500", "", ""]),
        ],
    },
    "cp-p049-15": {
        "name": "QUARTZ", "formula": "SiO2", "phase": [],
        "lines": [
            ("QUARTZ", ["41.5", "8.1145E+01", "1.828E-02", "-1.810E+05", "-6.985E+02", "5.406E-06", "298", "", ""]),
            ("SiO2", ["0.1", "", "", "", "", "", "844", "", ""]),
        ],
    },
}


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None
        self.in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.cell = []
            self.in_cell = True
        elif tag == "br" and self.in_cell:
            self.cell.append("\n")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.row is not None:
            self.row.append("".join(self.cell).strip())
            self.in_cell = False
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.in_cell:
            self.cell.append(data)


def latex_to_plain(s):
    s = re.sub(r"\\mathrm\{([^}]+)\}", r"\1", s)
    s = re.sub(r"\\text\{([^}]+)\}", r"\1", s)
    s = s.replace(r"\Box", "□").replace(r"\square", "□")
    s = re.sub(r"\\circ", "o", s)
    s = re.sub(r"\\cdot", "·", s)
    s = re.sub(r"\^\{([^}]+)\}", r"\1", s)
    s = re.sub(r"\^([+\-0-9A-Za-z])", r"\1", s)
    s = re.sub(r"_\{([^}]+)\}", r"\1", s)
    s = re.sub(r"_([A-Za-z0-9+\-]+)", r"\1", s)
    s = s.replace("{", "").replace("}", "").replace("\\", "")
    return s


def strip_mineru(text):
    if not text:
        return ""
    text = re.sub(r"\$([^$]+)\$", lambda m: latex_to_plain(m.group(1)), text)
    text = latex_to_plain(text)
    text = re.sub(r"[ \t]+", " ", text.replace("\xa0", " ")).strip()
    return text


def parse_number(raw):
    token = (raw or "").strip().replace("−", "-").replace("–", "-").replace("—", "-")
    if NUMBER.fullmatch(token):
        return float(token)
    return None


def token_in_image(raw, tess):
    if not raw:
        return False
    variants = {raw, raw.replace("−", "-"), raw.replace("–", "-")}
    return any(variant in tess for variant in variants)


def cell(raw, tess, *, high_temp=False):
    raw = (raw or "").strip()
    value = parse_number(raw)
    if not raw:
        return {"raw": "", "value": None, "ocr_suspect": False, "ocr_check": "blank"}
    agreed = token_in_image(raw, tess)
    if high_temp:
        valid = value is not None
        return {
            "raw": raw,
            "value": value,
            "ocr_suspect": (not valid) or (not agreed),
            "ocr_check": "raster_ocr_token_agreement" if agreed else "raster_ocr_token_disagreement",
        }
    return {
        "raw": raw,
        "value": value,
        "ocr_suspect": True,
        "ocr_check": "mineru_image_token_agreement" if agreed else "mineru_image_token_disagreement",
    }


def load_chunks():
    chunks = []
    for start, end in ((1, 90), (91, 180), (181, 270)):
        directory = MINERU / f"chunk-p{start:03}-p{end:03}"
        path = next(p for p in directory.glob("*_content_list.json") if "_v2" not in p.name)
        chunks.append((json.loads(path.read_text()), start))
    return chunks


def mineru_page(chunks, pdf_page):
    for data, start in chunks:
        if start <= pdf_page <= start + 89:
            return [item for item in data if item.get("page_idx") == pdf_page - start]
    return []


def mineru_table(chunks, pdf_page):
    for item in mineru_page(chunks, pdf_page):
        if item.get("type") == "table":
            parser = TableParser()
            parser.feed(item["table_body"])
            return parser.rows, item
    return [], None


def tess_text(pdf_page):
    path = TESS / f"p{pdf_page:03}.tess.txt"
    return path.read_text() if path.exists() else ""


def header_rows(rows, cp):
    if not rows:
        return 0
    if cp:
        return 1
    n = 1
    if len(rows) > 1:
        joined = " ".join(rows[1]).lower()
        if "j·" in joined or "mol" in joined or rows[1][0].strip() in {"g", "g"}:
            n = 2
    return n


def pad10(row):
    row = list(row)
    while len(row) < 10:
        row.append("")
    return row[:10]


def phases_from(*labels):
    text = " ".join(labels)
    found = re.findall(
        r"\([^)]*(?:REFERENCE STATE|AQUEOUS ION|IDEAL GAS|ideal gas|crystal|LIQUID|liquid|ION|ordered)[^)]*\)",
        text,
        re.I,
    )
    return found or None


def formula_from(name, formula_label, cp):
    label = strip_mineru(formula_label)
    name = strip_mineru(name)
    if "AQUEOUS" in name.upper() or name.endswith("+") or name.endswith("-"):
        return re.split(r"\s*\(", name, maxsplit=1)[0].strip() or None
    if label.upper().startswith("STD."):
        return re.split(r"\s*\(", name, maxsplit=1)[0].strip() or None
    label = re.sub(
        r"\s*\((?:[^)]*crystal|LIQUID|liquid|IDEAL GAS|ideal gas|ordered)[^)]*\)\s*$",
        "",
        label,
        flags=re.I,
    ).strip()
    return label or None


def build_line(label, values):
    line = label
    cells = []
    for raw in values:
        raw = raw or ""
        if not line.endswith("  "):
            line += "  " if line else ""
        if not raw:
            cells.append((None, None, ""))
            continue
        if not line.endswith(" "):
            line += "  "
        start = len(line)
        line += raw
        cells.append((start, len(line), raw))
    return line, cells


def semantic_rows(labels_and_values, columns, tess, *, high_temp=False):
    rows = []
    source_lines = []
    for index, (label, values) in enumerate(labels_and_values):
        line, spans = build_line(label, values)
        source_lines.append(line)
        body = {}
        for column, (start, end, raw) in zip(columns, spans):
            item = cell(raw, tess, high_temp=high_temp)
            item["start_offset"] = start
            item["end_offset"] = end
            body[column] = item
        rows.append({"label_raw": label, "cells": body, "source_line_index": index})
    return rows, "\n".join(source_lines)


def identity_checks(record, tess):
    issues = []
    checks = []
    if record["table_kind"] != "reference_state_298K" or not record["rows"]:
        return checks, issues
    first = record["rows"][0]["cells"]
    dg = first.get("formation_gibbs") or {}
    logk = first.get("log_kf") or {}
    tcell = (record.get("temperature_grid") or [{}])[0]
    t = tcell.get("value")
    if t and dg.get("value") is not None and logk.get("value") is not None:
        factor = 8.31446261815324 * t * math.log(10) / 1000

        def half_step(raw):
            return 0.5 * 10 ** (-len(raw.split(".")[1])) if raw and "." in raw else 0.5

        tolerance = half_step(dg.get("raw") or "") + factor * half_step(logk.get("raw") or "") + 0.01
        residual = dg["value"] + factor * logk["value"]
        disagreement = abs(residual) > tolerance
        checks.append({
            "identity": "delta_f_G + R*T*ln(10)*log_Kf = 0",
            "residual_kJ_per_mol": residual,
            "rounding_tolerance_kJ_per_mol": tolerance,
            "disagreement": disagreement,
        })
        if disagreement:
            issues.append(
                f"Formation Gibbs/log Kf identity disagreement: residual {residual} kJ/mol; "
                f"tolerance {tolerance}; detector only, raw unchanged"
            )
    return checks, issues


def ht_identity(rows):
    ambiguities = []
    for i, row in enumerate(rows):
        values = {key: row["cells"][key]["value"] for key in HT_COLUMNS}
        disagreements = []
        if all(values[key] is not None for key in ("entropy", "enthalpy_function", "gibbs_function")):
            residual = values["entropy"] - values["enthalpy_function"] - values["gibbs_function"]
            if abs(residual) > 0.0150001:
                disagreements.append({"identity": "S-H_function=G_function", "residual": residual})
                for key in ("entropy", "enthalpy_function", "gibbs_function"):
                    row["cells"][key]["ocr_suspect"] = True
        t, g, k = (values[key] for key in ("temperature", "formation_gibbs", "log_kf"))
        if t is not None and t > 0 and g is not None and k is not None:
            factor = 8.31451 * t * math.log(10) / 1000
            residual = g + factor * k
            if abs(residual) > 0.05 + factor * 0.005 + 0.00001:
                disagreements.append({"identity": "formation_G=-R*T*ln(10)*log_Kf", "residual_kJ_mol": residual})
                for key in ("temperature", "formation_gibbs", "log_kf"):
                    row["cells"][key]["ocr_suspect"] = True
        if disagreements:
            ambiguities.append({
                "kind": "identity_disagreement",
                "row": i,
                "temperature_raw": row["cells"]["temperature"]["raw"],
                "checks": disagreements,
            })
        suspects = [key for key, item in row["cells"].items() if item["ocr_suspect"]]
        if suspects:
            ambiguities.append({"kind": "ocr_suspect", "row": i, "columns": suspects})
    return ambiguities


def transcribe_summary(record, pair, tess, cp):
    columns = CP_COLUMNS if cp else REF_COLUMNS
    name = strip_mineru(pair[0][0])
    formula_label = strip_mineru(pair[1][0]) if len(pair) > 1 else ""
    labels_and_values = []
    for label, values in pair:
        labels_and_values.append((strip_mineru(label), [strip_mineru(v) for v in values]))
    rows, source_text = semantic_rows(labels_and_values, columns, tess)
    disagreements = []
    for row in rows:
        for key, item in row["cells"].items():
            if item["ocr_check"] == "mineru_image_token_disagreement" and item["raw"]:
                disagreements.append(f"{key}:{item['raw']}")
    record["name_as_published"] = name or record.get("name_candidate")
    record["formula_as_published"] = formula_from(name, formula_label, cp)
    record["phase_as_published"] = phases_from(name, formula_label)
    record["source_text"] = source_text
    record["rows"] = rows
    record["transcription_status"] = "transcribed_ocr_suspect"
    record.pop("name_candidate", None)
    record.pop("formula_candidate", None)
    identities, issues = identity_checks(record, tess)
    record["identity_checks"] = identities
    ambiguities = [
        "Pass-2 MinerU table cells cross-checked against the rendered page image / Tesseract. "
        "No silent numeric repair.",
    ]
    if disagreements:
        ambiguities.append("MinerU/image token disagreement (raw kept): " + ", ".join(disagreements[:24]))
    ambiguities.extend(issues)
    record["ambiguities"] = ambiguities
    if not cp:
        grid = record.get("temperature_grid") or []
        if not grid:
            record["temperature_grid"] = [{
                "raw": "298.15", "value": 298.15, "ocr_suspect": True, "ocr_check": "section_heading",
            }]
    return record, disagreements


def transcribe_page49(record, tess):
    spec = PAGE49[record["record_id"]]
    rows, source_text = semantic_rows(spec["lines"], CP_COLUMNS, tess)
    record["name_as_published"] = spec["name"]
    record["formula_as_published"] = spec["formula"]
    record["phase_as_published"] = spec["phase"] or None
    record["source_text"] = source_text
    record["rows"] = rows
    record["transcription_status"] = "transcribed_ocr_suspect"
    record.pop("name_candidate", None)
    record.pop("formula_candidate", None)
    record["identity_checks"] = []
    record["ambiguities"] = [
        "Pass-2: MinerU merged the two printed lines on this page; tokens taken from the "
        "rendered PDF page image. MinerU A-coefficients agreed with the image. No silent repair.",
    ]
    return record, []


def transcribe_high_temperature(record, chunks, tess):
    rows, item = mineru_table(chunks, record["pdf_page"])
    page_items = mineru_page(chunks, record["pdf_page"])
    headers = [strip_mineru(it.get("text") or "") for it in page_items if it.get("type") in {"header", "text"}]
    name = next((h for h in headers if h.isupper() and "THERMODYNAMIC" not in h and "FORMULA" not in h), "DIBORON TRIOXIDE")
    formula_line = next((h for h in headers if "Hexagonal" in h or "B2O3" in h.replace(" ", "")), "")
    formula = "B2O3"
    phase = "Hexagonal crystals 298.15 to melting point 723 K."
    data = []
    footer_rows = []
    for row in rows:
        first = strip_mineru(row[0] if row else "")
        if re.match(r"^\d", first):
            padded = (list(row) + [""] * 8)[:8]
            data.append([strip_mineru(c) for c in padded])
        elif first.lower().startswith("temp") or "Log Kf" in " ".join(row) or "Log  $K" in " ".join(row):
            continue
        else:
            footer_rows.append(row)
    ht_rows = []
    for i, values in enumerate(data):
        cells = {col: cell(val, tess, high_temp=True) for col, val in zip(HT_COLUMNS, values)}
        raw_line = "  ".join(values)
        raster = max(tess.splitlines(), key=lambda ln: sum(v in ln for v in values if v), default="")
        ht_rows.append({"source_line": i + 1, "raw": raw_line, "raster_reading": raster, "cells": cells})
    ambiguities = ht_identity(ht_rows)
    heading = "\n".join(h for h in headers if h)
    footer_bits = []
    for row in footer_rows:
        footer_bits.append(" ".join(strip_mineru(c) for c in row if strip_mineru(c)))
    footer = "\n".join(footer_bits)
    # MinerU concatenated the two printed molar-volume tokens; image has them on separate lines.
    footer = footer.replace("2.722 J·bar-127.22 cm3", "2.722 J·bar-1\n27.22 cm3")
    metadata = []
    for section, text in (("heading", heading), ("footer", footer)):
        for line in text.splitlines():
            if not line.strip():
                continue
            tokens = [tok for tok in line.split() if re.search(r"\d", tok) or re.fullmatch(r"[oO]+\.[oO]+", tok)]
            if not tokens:
                continue
            comparison = max(tess.splitlines(), key=lambda ln: sum(t in ln for t in tokens), default="")
            metadata.append({
                "section": section,
                "raw": line,
                "raster_reading": comparison,
                "tokens": [cell(tok, tess, high_temp=True) for tok in tokens],
            })
    for i, item_meta in enumerate(metadata):
        suspects = [tok["raw"] for tok in item_meta["tokens"] if tok["ocr_suspect"]]
        if suspects:
            ambiguities.append({"kind": "metadata_ocr_suspect", "metadata_index": i, "tokens": suspects})
    record.update({
        "name_as_published": name or "DIBORON TRIOXIDE",
        "formula_as_published": formula,
        "phase_as_published": phase,
        "heading_as_published": heading,
        "footer_as_published": footer,
        "units_as_published": [line for line in heading.splitlines() if "mol" in line or "K" in line],
        "metadata": metadata,
        "rows": ht_rows,
        "ambiguities": ambiguities,
        "transcription_status": "transcribed_with_ocr_ambiguities" if ambiguities else "transcribed",
    })
    return record, [a for a in ambiguities if a.get("kind") == "ocr_suspect"]


def pair_substances(rows, n_records, cp):
    hdr = header_rows(rows, cp)
    data = [pad10(r) for r in rows[hdr:]]
    if len(data) != 2 * n_records:
        raise ValueError(f"expected {2 * n_records} data rows, got {len(data)} (hdr={hdr})")
    pairs = []
    for i in range(n_records):
        a, b = data[2 * i], data[2 * i + 1]
        pairs.append(((a[0], a[1:]), (b[0], b[1:])))
    return pairs


def main():
    manifest = yaml.safe_load((ROOT / "manifest.yaml").read_text())
    untranscribed = [e for e in manifest["entries"] if e["transcription_status"] == "untranscribed"]
    protected = {}
    for entry in manifest["entries"]:
        if entry["transcription_status"] == "untranscribed":
            continue
        path = ROOT / entry["path"]
        protected[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    chunks = load_chunks()
    by_page = {}
    for entry in manifest["entries"]:
        rid = entry["record_id"]
        if not (rid.startswith("cp-") or rid.startswith("reference-")):
            continue
        by_page.setdefault(entry["source_locator"]["pdf_page"], []).append(entry)
    for pdf, entries in by_page.items():
        entries.sort(key=lambda e: int(e["record_id"].rsplit("-", 1)[-1]))

    outcomes = []
    for index, entry in enumerate(untranscribed, start=1):
        path = ROOT / entry["path"]
        record = json.loads(path.read_text())
        record_id = record["record_id"]
        tess = tess_text(record["pdf_page"])
        disagreements = []
        if record_id == "high-temperature-p176":
            record, disagreements = transcribe_high_temperature(record, chunks, tess)
        elif record_id in PAGE49:
            record, disagreements = transcribe_page49(record, tess)
        else:
            pdf = record["pdf_page"]
            page_entries = by_page[pdf]
            ordinal = int(record_id.rsplit("-", 1)[-1]) - 1
            cp = record["table_kind"] == "heat_capacity_coefficients"
            table_rows, _ = mineru_table(chunks, pdf)
            try:
                pairs = pair_substances(table_rows, len(page_entries), cp)
            except ValueError as exc:
                raise ValueError(f"{record_id} pdf {pdf}: {exc}") from exc
            record, disagreements = transcribe_summary(record, pairs[ordinal], tess, cp)
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        status = record["transcription_status"]
        n_rows = len(record.get("rows") or [])
        outcomes.append({
            "record_id": record_id,
            "status": "transcribed" if status != "untranscribed" else "untranscribed",
            "transcription_status": status,
            "rows": n_rows,
            "disagreements": len(disagreements),
            "reason": None if status != "untranscribed" else record.get("ambiguities"),
        })
        if index % 10 == 0 or index == len(untranscribed):
            print(
                f"!PROGRESS: ingest-usgs-b2131-pass2 — {index}/{len(untranscribed)} "
                f"{record_id} {status} rows={n_rows}",
                flush=True,
            )
    changed = [p for p, digest in protected.items() if hashlib.sha256(Path(p).read_bytes()).hexdigest() != digest]
    if changed:
        raise SystemExit(f"protected transcribed files changed: {changed[:8]}")
    (Path("/private/tmp/b2131-pass2-outcomes.json")).write_text(
        json.dumps(outcomes, indent=2, ensure_ascii=False) + "\n"
    )
    n_tr = sum(o["status"] == "transcribed" for o in outcomes)
    n_un = sum(o["status"] == "untranscribed" for o in outcomes)
    print(json.dumps({"transcribed": n_tr, "untranscribed": n_un, "n": len(outcomes)}), flush=True)


if __name__ == "__main__":
    main()
