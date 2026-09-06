#!/usr/bin/env python3
"""Harvest Burcat/Ruscic BURCAT.THR.txt into the compilation home.

Writes one JSON record per NASA-7 species/phase polynomial (and per
comment-only CAS stanza), a manifest, and copies the THR / XML / sidecar
bytes unchanged. Complete ingest: ions, condensed phases, exotic species,
and comment-only notes are retained. Ambiguities are recorded, never guessed.

Usage::

  python tools/harvest_burcat_compilation.py
  python tools/harvest_burcat_compilation.py --thr PATH --xml PATH --output-root PATH
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.reference_data.burcat import (  # noqa: E402
    COMPILATION_ROLE,
    COMPILATION_SOURCE,
    MANIFEST_SCHEMA,
    SCHEMA_VERSION,
    SIDECAR_REL,
    SOURCE_ID,
    SOURCE_REL,
    XML_REL,
    coverage_by_element,
    crosscheck_xml,
    feedstock_element_symbols,
    formulas_absent_from_janaf_and_glenn,
    parse_burcat_thr,
    parse_burcat_xml,
    peer_formula_sets,
    record_document,
)

BRIEF_THR = Path(
    "/Users/simonrowland/Repos/regolith-corpus-ctl/raw/burcat-third-millennium/BURCAT.THR.txt"
)
BRIEF_XML = Path(
    "/Users/simonrowland/Repos/regolith-corpus-ctl/raw/burcat-third-millennium/BURCAT_THR.xml"
)
BRIEF_SIDECAR = Path(
    "/Users/simonrowland/Repos/regolith-corpus-ctl/raw/burcat-third-millennium/sidecar.yaml"
)
OFFICIAL_URL = "https://respecth.elte.hu/burcat.php"


def cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thr", type=Path)
    parser.add_argument("--xml", type=Path)
    parser.add_argument("--sidecar", type=Path)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "data" / "literature" / "compilations" / "burcat",
    )
    return parser.parse_args()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def resolve_existing(candidates: list[Path], label: str) -> Path:
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(
        f"{label} not found; looked at: " + ", ".join(str(path) for path in candidates)
    )


def copy_if_needed(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != dest.resolve():
        shutil.copyfile(src, dest)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def build_manifest(
    parsed,
    *,
    source_sha256: str,
    xml_sha256: str,
    sidecar_sha256: str,
    coverage: dict[str, dict[str, Any]],
    xml_crosscheck: dict[str, Any],
    unique_formulas: list[dict[str, Any]],
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    by_formula: dict[str, list[str]] = defaultdict(list)
    total_coeff = 0
    total_amb = 0
    poly_count = 0
    comment_only = 0
    for rec in parsed.records:
        rel = f"data/literature/compilations/burcat/records/{rec.record_id}.json"
        total_coeff += rec.coefficient_count
        total_amb += len(rec.ambiguities)
        if rec.formula:
            by_formula[rec.formula].append(rec.record_id)
        if rec.record_kind == "nasa7_polynomial":
            poly_count += 1
        else:
            comment_only += 1
        entries.append(
            {
                "record_id": rec.record_id,
                "formula": rec.formula,
                "phase": rec.phase,
                "name_as_published": rec.name_as_published,
                "cas_as_published": rec.cas_as_published,
                "record_kind": rec.record_kind,
                "source": {
                    **COMPILATION_SOURCE,
                    "official_url": OFFICIAL_URL,
                    "sha256": source_sha256,
                    "path": SOURCE_REL,
                },
                "original_record_text_location": {
                    "path": SOURCE_REL,
                    "cas_line": rec.cas_line_number,
                    "header_line": rec.header_line_number,
                    "end_line": rec.end_line_number,
                },
                "coefficient_count": rec.coefficient_count,
                "interval_count": len(rec.intervals),
                "ambiguity_count": len(rec.ambiguities),
                "ambiguities": list(rec.ambiguities),
                "sha256_source_file": source_sha256,
                "path": rel,
            }
        )
    covered = [el for el, row in coverage.items() if row["has_record"]]
    uncovered = [el for el, row in coverage.items() if not row["has_record"]]
    return {
        "schema_version": MANIFEST_SCHEMA,
        "source_id": SOURCE_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "compilation_role": dict(COMPILATION_ROLE),
        "source": {
            **COMPILATION_SOURCE,
            "official_url": OFFICIAL_URL,
            "path": SOURCE_REL,
            "sha256": source_sha256,
            "xml_path": XML_REL,
            "xml_sha256": xml_sha256,
            "sidecar_path": SIDECAR_REL,
            "sidecar_sha256": sidecar_sha256,
            "record_format": (
                "NASA 7-coefficient 4-line form: 18-char name, 6-char date, "
                "four (A2,A3) composition slots, 1-char phase (G/S/L/C), "
                "T_low and T_high (2F10.3), quality letter + MW + card 1; "
                "then three 15-character × 5 coefficient cards (7 high-T a, "
                "7 low-T a, H298/R). T_common is not printed. Per-species "
                "CAS + comment block preserved verbatim. No unit conversion."
            ),
        },
        "corpus_status": {
            "scope": "complete BURCAT.THR.txt ingest (every 4-line polynomial and comment-only CAS stanza)",
            "xml_crosscheck": "BURCAT_THR.xml is the 2005 Thermodyne2XML snapshot; THR is current to 3 January 2023",
            "archives_zip": "not committed (brief exclusion)",
        },
        "summary": {
            "record_count": len(parsed.records),
            "polynomial_record_count": poly_count,
            "comment_only_record_count": comment_only,
            "coefficient_count": total_coeff,
            "parse_ambiguity_count": total_amb,
            "records_with_ambiguity_count": sum(
                1 for rec in parsed.records if rec.ambiguities
            ),
            "formula_count": len(by_formula),
            "source_schema_version": SCHEMA_VERSION,
        },
        "xml_crosscheck": xml_crosscheck,
        "feedstock_element_coverage": {
            "covered_elements": covered,
            "uncovered_elements": uncovered,
            "per_element": coverage,
        },
        "formulas_absent_from_janaf_and_nasa_glenn": {
            "count": len(unique_formulas),
            "formulas": unique_formulas,
        },
        "entries": entries,
    }


def main() -> int:
    args = cli()
    output_root = args.output_root.resolve()
    source_dir = output_root / "source"
    records_dir = output_root / "records"
    source_dir.mkdir(parents=True, exist_ok=True)
    records_dir.mkdir(parents=True, exist_ok=True)

    thr_src = resolve_existing(
        [p for p in (args.thr, source_dir / "BURCAT.THR.txt", BRIEF_THR) if p],
        "BURCAT.THR.txt",
    )
    xml_src = resolve_existing(
        [p for p in (args.xml, source_dir / "BURCAT_THR.xml", BRIEF_XML) if p],
        "BURCAT_THR.xml",
    )
    sidecar_src = resolve_existing(
        [p for p in (args.sidecar, source_dir / "sidecar.yaml", BRIEF_SIDECAR) if p],
        "sidecar.yaml",
    )
    dest_thr = source_dir / "BURCAT.THR.txt"
    dest_xml = source_dir / "BURCAT_THR.xml"
    dest_sidecar = source_dir / "sidecar.yaml"
    copy_if_needed(thr_src, dest_thr)
    copy_if_needed(xml_src, dest_xml)
    copy_if_needed(sidecar_src, dest_sidecar)

    thr_bytes = dest_thr.read_bytes()
    xml_bytes = dest_xml.read_bytes()
    sidecar_bytes = dest_sidecar.read_bytes()
    thr_digest = sha256_bytes(thr_bytes)
    xml_digest = sha256_bytes(xml_bytes)
    sidecar_digest = sha256_bytes(sidecar_bytes)

    text = thr_bytes.decode("utf-8-sig")
    parsed = parse_burcat_thr(
        text,
        source_path=SOURCE_REL,
        source_sha256=thr_digest,
    )
    xml_phases = parse_burcat_xml(dest_xml)
    xml_crosscheck = crosscheck_xml(parsed.records, xml_phases)
    parsed.xml_crosscheck = xml_crosscheck

    for stale in records_dir.glob("BU-*.json"):
        stale.unlink()
    for rec in parsed.records:
        write_json(records_dir / f"{rec.record_id}.json", record_document(rec))

    elements = feedstock_element_symbols()
    coverage = coverage_by_element(parsed.records, elements)
    janaf_formulas, glenn_formulas = peer_formula_sets()
    unique_formulas = formulas_absent_from_janaf_and_glenn(
        parsed.records, janaf_formulas, glenn_formulas
    )
    manifest = build_manifest(
        parsed,
        source_sha256=thr_digest,
        xml_sha256=xml_digest,
        sidecar_sha256=sidecar_digest,
        coverage=coverage,
        xml_crosscheck=xml_crosscheck,
        unique_formulas=unique_formulas,
    )
    manifest_path = output_root / "manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            manifest,
            sort_keys=False,
            allow_unicode=True,
            width=100,
        ),
        encoding="utf-8",
    )
    print(
        f"records={len(parsed.records)} "
        f"polynomials={manifest['summary']['polynomial_record_count']} "
        f"comment_only={manifest['summary']['comment_only_record_count']} "
        f"ambiguities={manifest['summary']['parse_ambiguity_count']} "
        f"xml_matched={xml_crosscheck['matched_count']} "
        f"xml_mismatch={xml_crosscheck['mismatch_count']} "
        f"unique_formulas={len(unique_formulas)} "
        f"sha256={thr_digest} "
        f"manifest={manifest_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
