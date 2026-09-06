"""Transcribe the bulletin OCR without repairing it; compare a raster reading."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE_ID = "robie-hemingway-1995-usgs-b2131"
NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?\Z")
COLUMNS = (
    "temperature", "cp", "entropy", "enthalpy_function", "gibbs_function",
    "formation_enthalpy", "formation_gibbs", "log_kf",
)
ROLE = {"engine_reference_input": True, "validation_measurement": False,
        "scoring_eligible": False, "battery_refusal": "gibbs_table_not_runtime_observable"}


def raster_lines(pdf: Path, page: int, scratch: Path) -> list[str]:
    prefix = scratch / f"raster-{page:03}"
    tsv = prefix.with_suffix(".tsv")
    if not tsv.exists() or tsv.stat().st_size < 100:
        subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-r", "180",
                        "-singlefile", "-png", str(pdf), str(prefix)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        subprocess.run(["tesseract", str(prefix.with_suffix(".png")), str(prefix),
                        "--psm", "6", "tsv"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    lines = {}
    with tsv.open() as stream:
        for word in csv.DictReader(stream, delimiter="\t"):
            if word["level"] == "5" and word["text"].strip():
                key = tuple(word[k] for k in ("block_num", "par_num", "line_num"))
                lines.setdefault(key, []).append(word["text"])
    return [" ".join(words) for words in lines.values()]


def cell(raw: str, raster: str) -> dict:
    valid = bool(NUMBER.fullmatch(raw))
    checked = raw in raster.split()
    return {"raw": raw, "value": float(raw) if valid else None,
            "ocr_suspect": not valid or not checked,
            "ocr_check": "raster_ocr_token_agreement" if checked else "raster_ocr_token_disagreement"}


def nearest_line(raw: str, lines: list[str]) -> str:
    return max(lines, key=lambda line: SequenceMatcher(None, raw.split(), line.split()).ratio(), default="")


def transcribe(pdf: Path, page: int, scratch: Path, digest: str) -> dict:
    text_path = scratch / f"{page:03}.txt"
    if not text_path.exists():
        subprocess.run(["pdftotext", "-f", str(page), "-l", str(page), "-layout",
                        str(pdf), str(text_path)], check=True)
    lines = text_path.read_text().splitlines()
    raster = raster_lines(pdf, page, scratch)
    title_index = next((i for i, line in enumerate(lines) if re.search(r"[FP]ormula[ .]+wt", line)), None)
    title = re.split(r"\s{2,}", lines[title_index].strip())[0] if title_index is not None else None
    starts = [i for i, line in enumerate(lines) if re.match(r"^\s*298[.,]15\s", line)]
    start = starts[0] if len(starts) == 1 else None
    end = start
    rows = []
    ambiguities = []
    if start is not None:
        for i in range(start, len(lines)):
            line = lines[i]
            if not line.strip():
                continue
            tokens = re.split(r"\s{2,}", line.strip())
            if len(tokens) != 8:
                end = i
                break
            comparison = nearest_line(line, raster)
            raster_tokens = comparison.split()
            values = {key: cell(raw, raster_tokens[j] if len(raster_tokens) == 8 else "")
                      for j, (key, raw) in enumerate(zip(COLUMNS, tokens))}
            rows.append({"source_line": i + 1, "raw": line, "raster_reading": comparison,
                         "cells": values})
            end = i + 1
    else:
        ambiguities.append({"kind": "untranscribed", "reason": "temperature table absent from OCR text layer"})
    # S - (H-H298)/T = -(G-H298)/T; rounding is 0.005 in each of three columns.
    # Formation Gibbs is in printed kJ/mol; R is the bulletin's 8.31451 J/mol/K.
    for i, row in enumerate(rows):
        cells = row["cells"]
        values = {key: value["value"] for key, value in cells.items()}
        disagreements = []
        if all(values[key] is not None for key in ("entropy", "enthalpy_function", "gibbs_function")):
            residual = values["entropy"] - values["enthalpy_function"] - values["gibbs_function"]
            if abs(residual) > 0.0150001:
                disagreements.append({"identity": "S-H_function=G_function", "residual": residual})
                for key in ("entropy", "enthalpy_function", "gibbs_function"):
                    cells[key]["ocr_suspect"] = True
        t, g, k = (values[key] for key in ("temperature", "formation_gibbs", "log_kf"))
        if t is not None and t > 0 and g is not None and k is not None:
            factor = 8.31451 * t * math.log(10) / 1000
            residual = g + factor * k
            if abs(residual) > 0.05 + factor * 0.005 + 0.00001:
                disagreements.append({"identity": "formation_G=-R*T*ln(10)*log_Kf", "residual_kJ_mol": residual})
                for key in ("temperature", "formation_gibbs", "log_kf"):
                    cells[key]["ocr_suspect"] = True
        if disagreements:
            ambiguities.append({"kind": "identity_disagreement", "row": i, "temperature_raw": cells["temperature"]["raw"], "checks": disagreements})
        suspects = [key for key, value in cells.items() if value["ocr_suspect"]]
        if suspects:
            ambiguities.append({"kind": "ocr_suspect", "row": i, "columns": suspects})
    heading_end = start if start is not None else len(lines)
    heading_start = title_index if title_index is not None else 1
    heading = "\n".join(lines[heading_start:heading_end]).strip()
    formula_match = re.search(r"^\s*([^:\n]+):\s*(.*)$", heading, re.M)
    formula = formula_match.group(1).strip() if formula_match else None
    phase = None
    if formula_match:
        phase = heading[formula_match.start(2):].split("\n\n", 1)[0].strip()
    if formula is None:
        ambiguities.append({"kind": "missing_formula", "reason": "not recovered from this page's OCR; not inferred from name"})
    footer = "\n".join(lines[end:]).strip() if end is not None else ""
    metadata = []
    # Store complete printed/OCR lines with tokens; do not reinterpret coefficients,
    # molar volumes, phase transitions, or units as a converted parameter schema.
    for section, section_lines in (("heading", lines[heading_start:heading_end]), ("footer", lines[end:] if end is not None else [])):
        for line in section_lines:
            if not line.strip():
                continue
            comparison = nearest_line(line, raster)
            tokens = [token for token in line.split() if re.search(r"\d", token) or re.fullmatch(r"[oO]+\.[oO]+", token)]
            if tokens:
                metadata.append({"section": section, "raw": line, "raster_reading": comparison,
                                 "tokens": [cell(token, comparison) for token in tokens]})
    for i, item in enumerate(metadata):
        suspects = [token["raw"] for token in item["tokens"] if token["ocr_suspect"]]
        if suspects:
            ambiguities.append({"kind": "metadata_ocr_suspect", "metadata_index": i, "tokens": suspects})
    identifier = f"high-temperature-p{page - 6:03}"
    record = {"record_id": identifier, "source_id": SOURCE_ID, "compilation_role": ROLE,
              "table_kind": "high_temperature", "table_number": None, "page": page - 6,
              "pdf_page": page, "name_as_published": title, "formula_as_published": formula,
              "phase_as_published": phase,
              "heading_as_published": heading, "footer_as_published": footer,
              "column_order": list(COLUMNS), "units_as_published": [line for line in lines[heading_start:heading_end] if "mol" in line or "aol" in line],
              "metadata": metadata, "rows": rows, "ambiguities": ambiguities,
              "transcription_status": "transcribed_with_ocr_ambiguities" if rows else "untranscribed"}
    destination = ROOT / "records" / f"{identifier}.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    print(f"!PROGRESS: ingest-usgs-b2131-b — {identifier}: {len(rows)} rows, {len(ambiguities)} ambiguities", flush=True)
    return {"record_id": identifier, "path": f"records/{identifier}.json", "formula": formula,
            "phase": record["phase_as_published"], "name_as_published": title,
            "source_locator": {"pdf_page": page, "printed_page": page - 6},
            "row_count": len(rows), "sha256": digest,
            "transcription_status": record["transcription_status"],
            "ambiguity_count": len(ambiguities), "ambiguities": ambiguities}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--scratch", type=Path, default=Path("/private/tmp/b2131-census"))
    parser.add_argument("--first", type=int, default=73)
    parser.add_argument("--last", type=int, default=402)
    args = parser.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(args.pdf.read_bytes()).hexdigest()
    with ThreadPoolExecutor(max_workers=4) as executor:
        entries = list(executor.map(lambda page: transcribe(args.pdf, page, args.scratch, digest), range(args.first, args.last + 1)))
    (args.scratch / f"index-{args.first}-{args.last}.json").write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n")
