"""Build the page-bounded, image-audited B689 partial ingest from native MinerU HTML."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import yaml

from tools.harvest_atct_compilation import feedstock_coverage
from tools.ingest_pankratz_1984_usbm_b677 import TableParser, parse_note_block, sha256


SOURCE_ID = "pankratz-1987-usbm-b689"
ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "data/literature/compilations" / SOURCE_ID
COLUMNS = ("temperature", "cp", "entropy", "gibbs_function", "enthalpy_increment",
           "delta_h", "delta_g", "log_k")
UNITS = ("K", "cal/mol·K", "cal/mol·K", "cal/mol·K", "kcal/mol", "kcal/mol", "kcal/mol", None)
NUMBER = re.compile(r"[+−-]?(?:\d{1,3}(?:,\d{3})+|\d+|\d*\.\d+)(?:\.\d+)?")


def _rows(html):
    parser = TableParser()
    parser.feed(html)
    return parser.rows


def _cell(raw, verified=False):
    token = raw.strip().removesuffix("*")
    value = float(token.replace(",", "").replace("−", "-")) if NUMBER.fullmatch(token) else None
    return {"raw": raw, "value": value, "ocr_suspect": not verified or value is None,
            "ocr_check": "image_verified" if verified else "not_image_verified",
            "footnote_markers": ["*"] if raw.endswith("*") else []}


def _identity_candidates(items, table_index):
    table = items[table_index]
    captions = table.get("table_caption", [])
    if not captions:
        captions = [x.get("text", "") for x in items[:table_index] if x["type"] == "text"]
    return [x for x in captions if not x.lstrip().startswith("[Formation:")
            and not x.lstrip().startswith("[Reaction:")]


def _structure_detectors(items, table_index):
    """Flag layout evidence for wraps/merges; never join or split identities automatically."""
    candidates = _identity_candidates(items, table_index)
    flags = []
    if len(candidates) != 2 or any("\n" in x or "<br" in x.lower() for x in candidates):
        flags.append({"kind": "identity_line_wrap_or_merge", "raw": candidates})
    if any("[Reaction:" in x or "[Formation:" in x for x in candidates):
        flags.append({"kind": "identity_and_reaction_merged", "raw": candidates})
    if candidates and len(re.findall(r"\((?:g|c|l|aq)(?:,[^()]*)?\)", candidates[0])) > 1:
        flags.append({"kind": "multiple_substance_tokens", "raw": candidates[0]})
    rows = _rows(items[table_index]["table_body"])
    for i, row in enumerate(rows[2:], 2):
        if len(row) != 8 or any("\n" in cell for cell in row):
            flags.append({"kind": "table_row_wrap_or_merge", "source_row_index": i, "raw": row})
    return flags


def _numeric_detectors(rows):
    flags = []
    for previous, current in zip(rows, rows[1:]):
        a, b = previous["cells"], current["cells"]
        if a["temperature"]["value"] is not None and b["temperature"]["value"] is not None:
            if b["temperature"]["value"] < a["temperature"]["value"]:
                flags.append({"kind": "temperature_decreases", "source_row_index": current["source_row_index"]})
        for column in COLUMNS[1:]:
            av, bv = a[column]["value"], b[column]["value"]
            if av and bv and 300 <= max(abs(av / bv), abs(bv / av)) <= 3000:
                flags.append({"kind": "adjacent_factor_approximately_1000", "column": column,
                              "source_row_index": current["source_row_index"], "raw": b[column]["raw"]})
    return flags


def _note_blocks(items, table_index):
    blocks = []
    for index, item in enumerate(items[table_index:], table_index):
        if item["type"] == "table":
            if index != table_index:
                blocks.extend(item.get("table_caption", []))
                blocks.append(item["table_body"])
            blocks.extend(item.get("table_footnote", []))
        elif item["type"] == "text":
            blocks.append(item["text"])
        elif item["type"] == "equation":
            blocks.append(item.get("text", ""))
    return blocks


def build(corpus):
    pdf = corpus / "raw" / SOURCE_ID / f"{SOURCE_ID}.pdf"
    sidecar = yaml.safe_load((pdf.parent / "sidecar.yaml").read_text())
    if sha256(pdf) != sidecar["sha256"]:
        raise ValueError("source PDF differs from acquisition checksum")
    source_path = corpus / "text" / SOURCE_ID / "mineru/chunk-p001-p040" / f"{SOURCE_ID}-p001-p040_content_list.json"
    content = json.loads(source_path.read_text())
    if {item["page_idx"] for item in content} != set(range(40)):
        raise ValueError("expected the complete first 40-page chunk")
    audits = [json.loads(line) for line in (DEST / "source/formula-audit.jsonl").read_text().splitlines()]
    if [a["pdf_page"] for a in audits] != list(range(7, 41)):
        raise ValueError("identity audit must cover every table page through PDF40")
    fixture = json.loads((DEST / "source/image-verified-fixture.json").read_text())
    pages = [{"pdf_page": pg + 1, "items": [x for x in content if x["page_idx"] == pg]} for pg in range(40)]
    source = {"database": "U.S. Bureau of Mines Bulletin 689", "version": "1987",
              "citation": sidecar["citation"], "official_url": "https://digital.library.unt.edu/ark:/67531/metadc38801/",
              "retrieved_url": sidecar["retrieved_url"], "licence": sidecar["licence"], "access_date": "2026-09-12"}
    records = []
    for audit in audits:
        pg = audit["pdf_page"]
        items = pages[pg - 1]["items"]
        table_index = next(i for i, x in enumerate(items) if x["type"] == "table")
        table = items[table_index]
        raw_rows = _rows(table["table_body"])
        verified_rows = fixture["complete_numeric_pages"].get(str(pg))
        if verified_rows is not None and raw_rows[2:] != verified_rows:
            raise ValueError(f"PDF{pg}: numeric transcription differs from independent image fixture")
        if any(len(row) != 8 for row in raw_rows[2:]):
            raise ValueError(f"PDF{pg}: row needs image adjudication")
        candidates = _identity_candidates(items, table_index)
        corrections = []
        if audit["status"] == "corrected":
            corrections.append({"record_id": audit["record_id"], "printed_page": audit["printed_page"],
                                "pdf_page": pg, "kind": audit["correction_kind"],
                                "field": "substance_identity", "ocr_token": candidates,
                                "printed_token": audit["formula_as_published"] + " / " + audit["name_as_published"],
                                "image_quote": audit["printed_quote"], "image_evidence": audit["image_evidence"]})
        rows = [{"source_row_index": i, "raw": row,
                 "cells": {column: _cell(token, verified_rows is not None) for column, token in zip(COLUMNS, row)}}
                for i, row in enumerate(raw_rows[2:], 2)]
        flags = _structure_detectors(items, table_index) + _numeric_detectors(rows)
        for correction in fixture["numeric_corrections"]:
            if correction["record_id"] != audit["record_id"]:
                continue
            row = next(r for r in rows if r["source_row_index"] == correction["source_row_index"])
            cell = row["cells"][correction["column"]]
            if cell["raw"] != correction["ocr_token"]:
                raise ValueError(f"stale numeric correction: {correction}")
            row["cells"][correction["column"]] = {
                **_cell(correction["printed_token"], True), "raw_ocr_token": cell["raw"]}
            corrections.append({**correction, "image_evidence": audit["image_evidence"]})
        note_blocks = _note_blocks(items, table_index)
        notes = parse_note_block("\n".join(note_blocks))
        notes["blocks_raw"] = note_blocks
        # Equations remain in their native OCR representation, never executable coefficients.
        notes["equation_transcription_status"] = "raw_ocr_only"
        metadata_unverified = audit["status"] == "unverified"
        ambiguities = flags + notes["ambiguities"]
        if verified_rows is None:
            ambiguities.append({"kind": "numeric_cells_not_image_verified"})
        if metadata_unverified:
            ambiguities.append({"kind": "unverified_printed_identity", "raw": audit["printed_quote"]})
        records.append({
            "record_id": audit["record_id"], "record_kind": audit["record_kind"],
            "formula": audit["formula"], "formula_as_published": audit["formula_as_published"],
            "name_as_published": audit["name_as_published"], "phase": audit["phase"],
            "metadata_ocr_token": candidates, "metadata_ocr_suspect": metadata_unverified,
            "printed_page": audit["printed_page"], "pdf_page": pg,
            "source_ref": {"path": "source/mineru-pages.jsonl", "line": pg, "item_index": table_index},
            "source_heading_raw": table.get("table_caption", []),
            "table_kind": "formation" if "Log Kf" in table["table_body"] else "reaction",
            "header_rows_raw": raw_rows[:2], "columns": list(COLUMNS), "units": dict(zip(COLUMNS, UNITS)),
            "rows": rows, "row_count": len(rows), "notes": notes,
            "structural_records": [{"record_kind": x["type"], "printed_form_ocr": x.get("text", "")}
                                   for x in items if x["type"] in {"header", "page_number", "footer"}],
            "corrections": corrections, "ambiguities": ambiguities,
            "transcription_status": "native_table_transcribed_with_explicit_ocr_coverage",
        })
    entries = []
    for record in records:
        entries.append({**{k: record[k] for k in ("record_id", "record_kind", "formula", "formula_as_published",
                                                  "name_as_published", "phase", "row_count", "metadata_ocr_suspect")},
                        "source": source, "source_locator": {"printed_page": record["printed_page"], "pdf_page": record["pdf_page"]},
                        "original_record_text_location": record["source_ref"], "sha256": sidecar["sha256"],
                        "path": f"records/{record['record_id']}.json",
                        "ambiguity_count": len(record["ambiguities"]), "ambiguities": record["ambiguities"],
                        "ocr_suspect_count": int(record["metadata_ocr_suspect"]) + int(record["notes"]["ocr_suspect"])
                        + sum(c["ocr_suspect"] for row in record["rows"] for c in row["cells"].values())})
    coverage = {"status": "partial", "pdf_page_count": 432, "decoded_pdf_pages": [1, 40],
                "ingested_printed_pages": [3, 36], "ingested_pdf_pages": [7, 40],
                "remaining_pdf_pages": [41, 432], "remaining_printed_pages": [37, 427],
                "remaining_note": "PDF432 is the unnumbered terminal scan; printed37–427 not transcribed. Front matter and methods PDF1–6 retained as source only.",
                "records_examined": len(audits),
                **{status: sum(a["status"] == status for a in audits) for status in ("matched", "corrected", "unverified")},
                "complete_numeric_image_audit_pdf_pages": [7],
                "numeric_cell_count": sum(len(r["rows"]) * 8 for r in records),
                "numeric_cells_image_verified": 96 + len(fixture["numeric_corrections"]),
                "per_page": [{"pdf_page": a["pdf_page"], "printed_page": a["printed_page"],
                              "record_id": a["record_id"], "identity_status": a["status"],
                              "numeric_status": "image_verified" if a["pdf_page"] == 7 else "unverified"} for a in audits]}
    manifest = {"schema_version": "literature_compilation_manifest.v1", "source_id": SOURCE_ID, "source": source,
                "compilation_role": {"engine_reference_input": True, "validation_measurement": False,
                                     "scoring_eligible": False, "oxide_rail_default": False,
                                     "non_oxide_policy": "warn_not_fail_closed", "intended_use": "Stage 0 cleanup chemistry"},
                "coverage": coverage, "record_count": len(records),
                "substance_count": sum(r["record_kind"] == "substance" for r in records),
                "feedstock_element_coverage": feedstock_coverage([{"formula": r["formula"] or ""} for r in records
                                                                 if r["record_kind"] == "substance"]),
                "source_files": [{"path": str(source_path.relative_to(corpus)), "sha256": sha256(source_path)},
                                 {"path": str(pdf.relative_to(corpus)), "sha256": sidecar["sha256"]}],
                "corrections": [c for r in records for c in r["corrections"]], "entries": entries}
    access_path = ROOT / "data/literature/compilations/access-status.yaml"
    access = yaml.safe_load(access_path.read_text())
    access["updated"] = "2026-09-12"
    access["sources"]["pankratz_1987_usbm_b689"] = {
        **source, "access": "public_domain_us_government_work", "local_status": "partial_ingest",
        "harvested_path": str(DEST.relative_to(ROOT)), "source_pdf_sha256": sidecar["sha256"],
        "harvested_record_count": len(records), "coverage": coverage,
        "note": "Non-oxide reference compilation; no battery scoring. Only first 40-page decode available locally."}
    artifacts = {DEST / "manifest.yaml": yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
                 DEST / "source/sidecar.yaml": yaml.safe_dump(sidecar, sort_keys=False, allow_unicode=True),
                 access_path: yaml.safe_dump(access, sort_keys=False, allow_unicode=True, width=120),
                 DEST / "source/mineru-pages.jsonl": "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in pages),
                 DEST / "census.json": json.dumps(coverage, indent=2) + "\n"}
    artifacts.update({DEST / f"records/{r['record_id']}.json": json.dumps(r, ensure_ascii=False, indent=2) + "\n" for r in records})
    return {str(path.relative_to(ROOT)): content for path, content in artifacts.items()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=Path(os.environ.get("REGOLITH_CORPUS_ROOT", Path.home() / "Repos/regolith-corpus")))
    args = parser.parse_args()
    print(json.dumps(build(args.corpus), ensure_ascii=False))
