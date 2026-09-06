"""Build the provenance/ambiguity manifest from native bulletin records."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import yaml

from tools.harvest_atct_compilation import feedstock_coverage
from tools.harvest_janaf_compilation import write_yaml


ROOT = Path(__file__).resolve().parent
SOURCE_ID = "robie-hemingway-1995-usgs-b2131"
ROLE = {"engine_reference_input": True, "validation_measurement": False,
        "scoring_eligible": False, "battery_refusal": "gibbs_table_not_runtime_observable"}


def cells(value):
    if isinstance(value, dict):
        if "raw" in value and "value" in value:
            yield value
        for child in value.values():
            yield from cells(child)
    elif isinstance(value, list):
        for child in value:
            yield from cells(child)


def build():
    sidecar = yaml.safe_load((ROOT / "source" / "sidecar.yaml").read_text())
    source = {"database": "USGS Bulletin 2131", "authors": "Robie, R. A.; Hemingway, B. S.",
              "version": "1995", "date_as_published": "1995", "official_url": sidecar["retrieved_url"],
              "doi": sidecar["doi"], "licence": sidecar["licence"], "access_date": "2026-09-06"}
    census = json.loads((ROOT / "census.json").read_text())
    entries = []
    records = []
    suspect_examples = []
    counts = Counter()
    for directory in ("auxiliary", "summary", "records"):
        for path in sorted((ROOT / directory).glob("*.json")):
            record = json.loads(path.read_text())
            if "record_id" not in record:
                continue
            records.append(record)
            numeric = list(cells(record))
            suspects = [cell for cell in numeric if cell["ocr_suspect"]]
            counts["numeric_cell_count"] += len(numeric)
            counts["parsed_value_count"] += sum(cell["value"] is not None for cell in numeric)
            counts["ocr_suspect_count"] += len(suspects)
            status = record.get("transcription_status", "transcribed_with_ocr_ambiguities" if record["ambiguities"] else "transcribed")
            counts["untranscribed_record_count"] += status == "untranscribed"
            ambiguities = record["ambiguities"]
            counts["identity_check_disagreement_count"] += sum(
                (isinstance(item, dict) and item.get("kind") == "identity_disagreement")
                or (isinstance(item, str) and "identity disagreement" in item.lower())
                for item in ambiguities
            )
            entries.append({"record_id": record["record_id"], "formula": record.get("formula_as_published"),
                            "phase": record.get("phase_as_published"), "name_as_published": record.get("name_as_published"),
                            "source": source, "sha256": sidecar["sha256"], "path": str(path.relative_to(ROOT)),
                            "source_locator": {"pdf_page": record["pdf_page"], "printed_page": record["page"],
                                               "table_number": record.get("table_number")},
                            "original_record_text_location": str(path.relative_to(ROOT)),
                            "row_count": len(record.get("rows", [])), "ocr_suspect_count": len(suspects),
                            "transcription_status": status, "ambiguity_count": len(ambiguities), "ambiguities": ambiguities})
            if suspects and len(suspect_examples) < 12:
                suspect_examples.append({"record_id": record["record_id"], "tokens": [cell["raw"] for cell in suspects[:4]]})
    counts["record_count"] = len(records)
    counts["transcribed_record_count"] = len(records) - counts["untranscribed_record_count"]
    counts["record_ambiguity_count"] = sum(entry["ambiguity_count"] for entry in entries)
    manifest = {"schema_version": "literature_compilation_manifest.v1", "source_id": SOURCE_ID,
                "source": source, "compilation_role": ROLE,
                "source_files": [{"path": f"raw/{SOURCE_ID}/{SOURCE_ID}.pdf", "sha256": sidecar["sha256"],
                                  "storage": "external read-only regolith-corpus-ctl corpus"},
                                 {"path": "source/sidecar.yaml", "sha256": hashlib.sha256((ROOT / "source/sidecar.yaml").read_bytes()).hexdigest()}],
                "census": census, "summary": dict(counts), "ocr_suspect_examples": suspect_examples,
                "ocr_policy": {"primary": "pdftotext -layout, separately per PDF page; scratch text outside repository",
                               "second_reading": "Tesseract on rendered printed pages; MinerU absent at start",
                               "numeric_repairs": False, "identity_checks": "detectors only; never correct values",
                               "manual_verification": "atomic weights and constants; other records retain automated raster-reading flags"},
                "feedstock_element_coverage": feedstock_coverage([{"formula": record.get("formula_as_published") or ""} for record in records]),
                "untranscribed": [{"record_id": entry["record_id"], "page_range": [entry["source_locator"]["printed_page"]] * 2,
                                   "pdf_page_range": [entry["source_locator"]["pdf_page"]] * 2,
                                   "reasons": entry["ambiguities"]} for entry in entries if entry["transcription_status"] == "untranscribed"],
                "entries": entries}
    write_yaml(ROOT / "manifest.yaml", manifest)
    print(json.dumps(manifest["summary"], sort_keys=True))


if __name__ == "__main__":
    build()
