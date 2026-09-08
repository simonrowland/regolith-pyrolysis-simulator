"""Transcribe USBM Bulletin 584 MinerU tables with raster OCR checks."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import yaml

from tools.harvest_atct_compilation import feedstock_coverage


SOURCE_ID = "kelley-1960-usbm-b584"
ROOT = Path(__file__).resolve().parent
CORPUS_RAW = Path("/Users/simonrowland/Repos/regolith-corpus/raw") / SOURCE_ID
MINERU_ROOT = Path("/Users/simonrowland/Repos/regolith-corpus/text") / SOURCE_ID / "mineru"
PDF = CORPUS_RAW / f"{SOURCE_ID}.pdf"
EXPECTED_TABLES = 893
ACCESS_DATE = "2026-09-07"
GENERIC_SUBHEADINGS = {"ELEMENT"}


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def parse_html_table(source: str) -> list[list[str]]:
    parser = TableParser()
    parser.feed(source)
    return parser.rows


def flatten_mineru(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(flatten_mineru(item) for item in value)
    if isinstance(value, dict):
        for key in ("content", "math_content", "paragraph_content", "title_content"):
            if key in value:
                return flatten_mineru(value[key])
    return ""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_yaml(path: Path, value: Any) -> None:
    path.write_text(
        yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=120),
        encoding="utf-8",
    )


def build_census(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    markdown = MINERU_ROOT / "chunk-p001-p040" / f"{SOURCE_ID}-p001-p040.md"
    lines = markdown.read_text(encoding="utf-8").splitlines()
    sequence: list[tuple[int, str, str | None]] = []
    for source_line, line in enumerate(lines[49:521], start=50):
        if "<table" in line:
            for cells in parse_html_table(line):
                joined = " ".join(cells[:-1]).strip()
                if "content and entropy" in joined.lower():
                    sequence.append((source_line, joined, cells[-1].strip()))
        elif "content" in line.lower() and "entropy" in line.lower():
            sequence.append((source_line, line.strip(), None))
    if len(sequence) != EXPECTED_TABLES:
        raise RuntimeError(f"bulletin table-list census has {len(sequence)} rows, expected {EXPECTED_TABLES}")
    census = []
    for number, ((source_line, raw, html_page), block) in enumerate(zip(sequence, blocks, strict=True), start=1):
        title_start = raw.lower().find("heat content and entropy of")
        title_with_page = raw[title_start:] if title_start >= 0 else raw
        trailing = re.search(r"\s+([^\s]+)\s*$", title_with_page)
        page_raw = html_page or (trailing.group(1) if trailing else None)
        title = re.sub(r"(?:\s+[0-9bBoOIlS.]+){1,2}\s*$", "", title_with_page).strip()
        prefix = raw[:title_start].strip() if title_start >= 0 else ""
        number_match = re.search(r"([0-9bBoOIlS]+)\.?", prefix)
        number_raw = number_match.group(1) if number_match else None
        census_ambiguities = []
        if number_raw != str(number):
            census_ambiguities.append(
                {
                    "kind": "census_table_number_ocr_disagreement",
                    "expected_ordinal": number,
                    "raw_token": number_raw,
                    "note": "Ordinal follows the bulletin list position; raw OCR token retained.",
                }
            )
        try:
            page_value = int(str(page_raw).rstrip("."))
        except (TypeError, ValueError):
            page_value = None
        if page_value != block["printed_page"]:
            census_ambiguities.append(
                {
                    "kind": "census_page_ocr_disagreement",
                    "physical_printed_page": block["printed_page"],
                    "raw_token": page_raw,
                    "note": "Physical page comes from the page-aligned MinerU chunk; raw table-list OCR retained.",
                }
            )
        census.append(
            {
                "table_number": number,
                "table_number_ocr_raw": number_raw,
                "title_as_published": title,
                "page": block["printed_page"],
                "page_ocr_raw": page_raw,
                "ambiguities": census_ambiguities,
                "census_source": {"pdf_page_range": [4, 14], "mineru_markdown_line": source_line},
            }
        )
    return census


def mineru_blocks() -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    heading_context: list[str] = []
    paths = sorted(MINERU_ROOT.glob("chunk-p*-p*/*content_list_v2.json"))
    for path in paths:
        match = re.search(r"chunk-p(\d+)-p", path.parent.name)
        if not match:
            continue
        first_page = int(match.group(1))
        pages = json.loads(path.read_text(encoding="utf-8"))
        for page_offset, items in enumerate(pages):
            pdf_page = first_page + page_offset
            for item_index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "title":
                    heading = flatten_mineru(item.get("content", {})).strip()
                    if heading:
                        heading_context.append(heading)
                        heading_context = heading_context[-4:]
                    continue
                if item.get("type") != "table" or pdf_page <= 14:
                    continue
                content = item.get("content", {})
                html = content.get("html", "")
                rows = parse_html_table(html)
                if len(rows) < 2 or max((len(row) for row in rows), default=0) < 3:
                    continue
                image_relative = content.get("image_source", {}).get("path")
                image_path = path.parent / image_relative
                blocks.append(
                    {
                        "pdf_page": pdf_page,
                        "printed_page": pdf_page - 16,
                        "item_index": item_index,
                        "caption_raw": flatten_mineru(content.get("table_caption", [])).strip(),
                        "footnotes_raw": flatten_mineru(content.get("table_footnote", [])).strip(),
                        "html": html,
                        "image_path": str(image_path.relative_to(MINERU_ROOT)),
                        "image_sha256": sha256(image_path),
                        "heading_context_as_published": list(heading_context),
                    }
                )
    if len(blocks) != EXPECTED_TABLES:
        raise RuntimeError(f"expected {EXPECTED_TABLES} numeric table blocks, found {len(blocks)}")
    return blocks


def run_tesseract(block: dict[str, Any]) -> str:
    image = MINERU_ROOT / block["image_path"]
    result = subprocess.run(
        ["tesseract", str(image), "stdout", "--psm", "6"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return " ".join(result.stdout.split()) if result.returncode == 0 else ""


def add_raster_readings(blocks: list[dict[str, Any]], workers: int) -> None:
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(run_tesseract, block): index for index, block in enumerate(blocks)}
        for future in as_completed(futures):
            index = futures[future]
            blocks[index]["raster_ocr"] = future.result()
            print(f"TABLE {index + 1:03d}/{EXPECTED_TABLES}: raster OCR checked", flush=True)


NUMBER = re.compile(r"^[^0-9+\-]*([+\-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?:\.{2,}|[^0-9]*)$")
CONFUSABLE = re.compile(r"[lIOSB]", re.IGNORECASE)


def normalized_number(token: str) -> str:
    return token.replace(",", "").lstrip("+")


def raw_numeric_token(raw: str) -> tuple[str | None, bool]:
    stripped = raw.strip().replace("−", "-")
    confusion_surface = re.sub(r"\([^)]*\)", "", stripped)
    has_confusable = bool(CONFUSABLE.search(confusion_surface))
    match = None if has_confusable else NUMBER.fullmatch(stripped)
    token = match.group(1) if match else None
    return token, has_confusable


def raster_alignment(raw_rows: list[list[str]], raster_ocr: str) -> set[tuple[int, int]]:
    source = []
    coordinates = []
    for row_index, row in enumerate(raw_rows[1:], start=1):
        for column_index, raw in enumerate(row):
            token, _ = raw_numeric_token(raw)
            if token is not None:
                source.append(normalized_number(token))
                coordinates.append((row_index, column_index))
    raster = [
        normalized_number(item)
        for item in re.findall(r"[+\-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", raster_ocr)
    ]
    matcher = difflib.SequenceMatcher(a=source, b=raster, autojunk=False)
    return {
        coordinates[source_index]
        for source_index, _raster_index, size in matcher.get_matching_blocks()
        for source_index in range(source_index, source_index + size)
    }


def parse_cell(raw: str, raster_agrees: bool) -> dict[str, Any]:
    stripped = raw.strip().replace("−", "-")
    token, has_confusable = raw_numeric_token(raw)
    value = float(token.replace(",", "")) if token else None
    agrees = token is not None and raster_agrees
    suspect = bool(stripped) and (token is None or not agrees or has_confusable)
    cell: dict[str, Any] = {
        "raw": raw,
        "value": value,
        "ocr_suspect": suspect,
        "ocr_check": "raster_ocr_token_agreement" if agrees else "raster_ocr_token_disagreement",
    }
    if token is not None and token != stripped:
        cell["numeric_token"] = token
        cell["footnote_or_leader_marker"] = stripped.replace(token, "", 1)
    return cell


def substance_fields(title: str) -> tuple[str, str | None, str]:
    match = re.search(r"entropy\s+of\s+(.+)$", title, re.IGNORECASE)
    substance = match.group(1).strip() if match else title.strip()
    substance = re.sub(r"\s*\.\s*$", "", substance).strip()
    phase_match = re.search(r"(\([^()]+\))(?P<closing>\}?)\s*\$?\s*$", substance)
    phase = phase_match.group(1) if phase_match else None
    formula = (
        (substance[: phase_match.start()] + phase_match.group("closing")).strip(" $")
        if phase_match
        else substance.strip(" $")
    )
    return formula, phase, substance


def base_state(caption: str) -> str | None:
    match = re.search(r"\[Base,\s*(.*?)\]", caption, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else None


def identity_disagreements(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    usable = []
    for row in rows:
        cells = row["cells"]
        values = [cells[key]["value"] for key in ("temperature", "heat_content", "entropy_increment")]
        if all(value is not None for value in values):
            usable.append((values[0], values[1], values[2], row["source_row_index"], row["panel_index"]))
    usable.sort(key=lambda item: item[0])
    disagreements = []
    for left, right in zip(usable, usable[1:]):
        t1, h1, s1, *_ = left
        t2, h2, s2, *_ = right
        if t2 <= t1 or t1 <= 0:
            continue
        log_mean_t = (t2 - t1) / math.log(t2 / t1)
        expected_ds = (h2 - h1) / log_mean_t
        residual = (s2 - s1) - expected_ds
        if abs(residual) > 0.15:
            disagreements.append(
                {
                    "kind": "identity_disagreement",
                    "detector": "finite_difference_dS_equals_dH_over_T",
                    "temperature_pair": [t1, t2],
                    "entropy_residual_cal_per_deg_mole": round(residual, 4),
                    "source_rows": [left[3:], right[3:]],
                    "note": "Detector only; printed tokens retained unchanged.",
                }
            )
    return disagreements


def build_record(number: int, census: dict[str, Any], block: dict[str, Any]) -> dict[str, Any]:
    raw_rows = parse_html_table(block["html"])
    aligned = raster_alignment(raw_rows, block["raster_ocr"])
    header = raw_rows[0]
    logical_rows = []
    all_source_rows = []
    for source_row_index, raw_row in enumerate(raw_rows):
        all_source_rows.append(raw_row)
        if source_row_index == 0:
            continue
        for panel_index in range(0, len(raw_row), 3):
            panel = raw_row[panel_index : panel_index + 3]
            if len(panel) < 3 or not any(cell.strip() for cell in panel):
                continue
            cells = {
                "temperature": parse_cell(panel[0], (source_row_index, panel_index) in aligned),
                "heat_content": parse_cell(panel[1], (source_row_index, panel_index + 1) in aligned),
                "entropy_increment": parse_cell(panel[2], (source_row_index, panel_index + 2) in aligned),
            }
            logical_rows.append(
                {
                    "source_row_index": source_row_index,
                    "panel_index": panel_index // 3,
                    "raw": panel,
                    "cells": cells,
                }
            )
    caption_substance_match = re.search(
        r"entropy\s+of\s+(.+?)(?:[\[(]?Base(?:,|\s)|$)", block["caption_raw"], re.IGNORECASE | re.DOTALL
    )
    metadata_title = (
        f"Heat content and entropy of {caption_substance_match.group(1).strip()}"
        if caption_substance_match
        else census["title_as_published"]
    )
    formula, phase, substance = substance_fields(metadata_title)
    caption_match = re.search(r"TABLE\s*(\d+)", block["caption_raw"], re.IGNORECASE)
    printed_table_number = int(caption_match.group(1)) if caption_match else None
    ambiguities: list[dict[str, Any]] = list(census.get("ambiguities", []))
    if printed_table_number != number:
        ambiguities.append(
            {
                "kind": "table_number_disagreement",
                "census_table_number": number,
                "caption_ocr_table_number": printed_table_number,
                "caption_raw": block["caption_raw"],
                "note": "Record identity follows sequential bulletin table-list census; caption retained unchanged.",
            }
        )
    census_formula, census_phase, census_substance = substance_fields(census["title_as_published"])
    canonical = lambda value: re.sub(r"[^a-z0-9]+", "", re.sub(r"\\(?:mathrm|operatorname|text)", "", value).lower())
    if canonical(census_substance) != canonical(substance):
        ambiguities.append(
            {
                "kind": "census_caption_substance_disagreement",
                "census_raw": census_substance,
                "caption_raw": substance,
                "note": "Caption is the record metadata source; both OCR readings retained without repair.",
            }
        )
    ambiguities.extend(identity_disagreements(logical_rows))
    name = next(
        (heading for heading in reversed(block["heading_context_as_published"]) if heading not in GENERIC_SUBHEADINGS),
        substance,
    )
    temperature_grid = [row["cells"]["temperature"] for row in logical_rows]
    return {
        "record_id": f"table-{number:03d}",
        "table_kind": "heat_content_and_entropy",
        "census_table_number": number,
        "table_number_as_printed_or_ocr": printed_table_number,
        "formula_as_published": formula,
        "name_as_published": name,
        "heading_context_as_published": block["heading_context_as_published"],
        "substance_as_published": substance,
        "phase_as_published": phase,
        "state_as_published": base_state(block["caption_raw"]),
        "page": census["page"],
        "pdf_page": block["pdf_page"],
        "caption_raw": block["caption_raw"],
        "footnotes_raw": block["footnotes_raw"],
        "column_labels_as_published": header,
        "units_as_published": header,
        "temperature_grid": temperature_grid,
        "rows": logical_rows,
        "source_rows": all_source_rows,
        "source_ref": {"path": "source/mineru-tables.jsonl", "line": number},
        "source_image": {"path": block["image_path"], "sha256": block["image_sha256"]},
        "transcription_status": "transcribed_with_ocr_ambiguities" if ambiguities else "transcribed",
        "ambiguities": ambiguities,
        "corrections": [],
    }


def all_cells(value: Any):
    if isinstance(value, dict):
        if "raw" in value and "value" in value:
            yield value
        for child in value.values():
            yield from all_cells(child)
    elif isinstance(value, list):
        for child in value:
            yield from all_cells(child)


def write_access_status(sidecar: dict[str, Any]) -> None:
    path = ROOT.parent / "access-status.yaml"
    status = yaml.safe_load(path.read_text(encoding="utf-8"))
    status["updated"] = ACCESS_DATE
    status["sources"]["kelley_1960_usbm_b584"] = {
        "citation": sidecar["citation"],
        "report_number": "U.S. Bureau of Mines Bulletin 584",
        "access": "public_domain_us_government_work",
        "official_url": sidecar["located_by"]["official_url"],
        "retrieved_url": sidecar["retrieved_url"],
        "licence_basis": "United States Bureau of Mines government work; public domain in the United States.",
        "access_date": ACCESS_DATE,
        "local_status": "complete_ingest",
        "harvested_path": f"data/literature/compilations/{SOURCE_ID}/",
        "source_pdf_sha256": sidecar["sha256"],
        "source_pdf_bytes": sidecar["size"],
        "harvested_record_count": EXPECTED_TABLES,
        "note": "Assessed heat-content and entropy tables; reference input only, never measured or battery-scored.",
    }
    write_yaml(path, status)


def build(workers: int) -> None:
    sidecar = yaml.safe_load((CORPUS_RAW / "sidecar.yaml").read_text(encoding="utf-8"))
    if sha256(PDF) != sidecar["sha256"]:
        raise RuntimeError("PDF SHA-256 differs from acquisition sidecar")
    blocks = mineru_blocks()
    source_dir = ROOT / "source"
    records_dir = ROOT / "records"
    source_dir.mkdir(parents=True, exist_ok=True)
    records_dir.mkdir(parents=True, exist_ok=True)
    source_path = source_dir / "mineru-tables.jsonl"
    if source_path.exists():
        cached = [json.loads(line) for line in source_path.read_text(encoding="utf-8").splitlines()]
        cached_native = [{key: value for key, value in block.items() if key != "raster_ocr"} for block in cached]
        if (
            len(cached) == EXPECTED_TABLES
            and all("raster_ocr" in block for block in cached)
            and cached_native == blocks
        ):
            blocks = cached
        else:
            add_raster_readings(blocks, workers)
    else:
        add_raster_readings(blocks, workers)
    census_entries = build_census(blocks)
    write_yaml(source_dir / "sidecar.yaml", sidecar)
    with source_path.open("w", encoding="utf-8") as stream:
        for block in blocks:
            stream.write(json.dumps(block, ensure_ascii=False, separators=(",", ":")) + "\n")
    records = []
    for number, (census, block) in enumerate(zip(census_entries, blocks, strict=True), start=1):
        record = build_record(number, census, block)
        records.append(record)
        (records_dir / f"table-{number:03d}.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    suspect_examples = []
    summary = {
        "record_count": len(records),
        "transcribed_record_count": len(records),
        "untranscribed_record_count": 0,
        "numeric_cell_count": 0,
        "parsed_value_count": 0,
        "ocr_suspect_count": 0,
        "identity_check_disagreement_count": 0,
        "correction_count": 0,
    }
    entries = []
    source = {
        "database": "U.S. Bureau of Mines Bulletin 584",
        "authors": "Kelley, K. K.",
        "version": "1960",
        "date_as_published": "1960",
        "official_url": sidecar["located_by"]["official_url"],
        "retrieved_url": sidecar["retrieved_url"],
        "licence": sidecar["licence"],
        "access_date": ACCESS_DATE,
    }
    for record in records:
        cells = [cell for row in record["rows"] for cell in row["cells"].values()]
        suspects = [cell for cell in cells if cell["ocr_suspect"]]
        summary["numeric_cell_count"] += len(cells)
        summary["parsed_value_count"] += sum(cell["value"] is not None for cell in cells)
        summary["ocr_suspect_count"] += len(suspects)
        summary["identity_check_disagreement_count"] += sum(
            item.get("kind") == "identity_disagreement" for item in record["ambiguities"]
        )
        if suspects and len(suspect_examples) < 15:
            suspect_examples.append(
                {"record_id": record["record_id"], "tokens": [cell["raw"] for cell in suspects[:5]]}
            )
        entries.append(
            {
                "record_id": record["record_id"],
                "formula": record["formula_as_published"],
                "phase": record["phase_as_published"],
                "name_as_published": record["name_as_published"],
                "source": source,
                "original_record_text_location": record["source_ref"],
                "source_locator": {
                    "pdf_page": record["pdf_page"],
                    "printed_page": record["page"],
                    "table_number": record["table_number_as_printed_or_ocr"],
                    "census_table_number": record["census_table_number"],
                },
                "path": f"records/{record['record_id']}.json",
                "row_count": len(record["rows"]),
                "ocr_suspect_count": len(suspects),
                "ambiguity_count": len(record["ambiguities"]),
                "ambiguities": record["ambiguities"],
                "sha256": sidecar["sha256"],
            }
        )
    census = {
        "basis": "Bulletin's TABLES list, PDF pages 4-14",
        "record_count": len(census_entries),
        "table_number_range": [1, EXPECTED_TABLES],
        "printed_page_range": [min(item["page"] for item in census_entries), max(item["page"] for item in census_entries)],
        "pdf_table_page_range": [min(record["pdf_page"] for record in records), max(record["pdf_page"] for record in records)],
        "record_ids": [record["record_id"] for record in records],
        "entries": census_entries,
    }
    manifest = {
        "schema_version": "literature_compilation_manifest.v1",
        "source_id": SOURCE_ID,
        "source": source,
        "compilation_role": {
            "engine_reference_input": True,
            "validation_measurement": False,
            "scoring_eligible": False,
            "battery_refusal": "assessed_compilation_not_runtime_observable",
        },
        "source_files": [
            {"path": f"raw/{SOURCE_ID}/{SOURCE_ID}.pdf", "sha256": sidecar["sha256"], "storage": "external read-only corpus"},
            {"path": "source/mineru-tables.jsonl", "sha256": sha256(source_dir / "mineru-tables.jsonl")},
            {"path": "source/sidecar.yaml", "sha256": sha256(source_dir / "sidecar.yaml")},
        ],
        "census": census,
        "summary": summary,
        "ocr_policy": {
            "primary": "MinerU table HTML from six page-aligned chunks",
            "second_reading": "Tesseract on each MinerU page-image table crop; disagreements remain suspect",
            "numeric_repairs": False,
            "identity_checks": "finite-difference thermodynamic detector only; never corrects values",
        },
        "ocr_suspect_examples": suspect_examples,
        "corrections": [],
        "untranscribed": [],
        "feedstock_element_coverage": feedstock_coverage(
            [{"formula": record["formula_as_published"]} for record in records]
        ),
        "entries": entries,
    }
    write_yaml(ROOT / "manifest.yaml", manifest)
    (ROOT / "census.json").write_text(json.dumps(census, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_access_status(sidecar)
    print(json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ocr-workers", type=int, default=8)
    arguments = parser.parse_args()
    build(arguments.ocr_workers)
