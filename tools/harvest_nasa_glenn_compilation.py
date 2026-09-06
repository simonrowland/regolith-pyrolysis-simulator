#!/usr/bin/env python3
"""Harvest NASA Glenn / CEA thermo.inp into the compilation home.

Writes one JSON record per species/phase, a manifest, and copies the
source ``thermo.inp`` bytes unchanged. Complete ingest: ions, condensed
phases, isotopes, nobles, assigned-enthalpy reactants, and inverted
T-intervals are all retained. Ambiguities are recorded, never guessed.

Usage::

  python tools/harvest_nasa_glenn_compilation.py
  python tools/harvest_nasa_glenn_compilation.py --thermo PATH --output-root PATH
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

from simulator.reference_data.nasa_glenn import (  # noqa: E402
    COMPILATION_ROLE,
    COMPILATION_SOURCE,
    MANIFEST_SCHEMA,
    SCHEMA_VERSION,
    SOURCE_ID,
    SOURCE_REL,
    ThermoInpFile,
    coverage_by_element,
    feedstock_element_symbols,
    parse_thermo_inp,
    record_document,
)

# Brief-specified snapshot (main checkout, gitignored research dir).
BRIEF_THERMO = Path(
    "/Users/simonrowland/Library/CloudStorage/Dropbox/Starship Mission Design/"
    "Regolith Processing/regolith-pyrolysis-simulator/docs-private/research/"
    "2026-08-01-cea-sweep/thermo.inp"
)
EXTRACT_PATH = ROOT / "data" / "literature" / "extracts" / "nasa-cea-thermo.yaml"
CEA_DOWNLOAD_URL = "https://www.grc.nasa.gov/www/CEAWeb/"


def cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thermo", type=Path)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "data" / "literature" / "compilations" / "nasa-glenn",
    )
    parser.add_argument("--extract", type=Path, default=EXTRACT_PATH)
    return parser.parse_args()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def resolve_thermo(args_thermo: Path | None, output_root: Path) -> Path:
    candidates = []
    if args_thermo is not None:
        candidates.append(args_thermo)
    candidates.append(output_root / "source" / "thermo.inp")
    candidates.append(ROOT / "docs-private/research/2026-08-01-cea-sweep/thermo.inp")
    candidates.append(BRIEF_THERMO)
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(
        "thermo.inp not found; looked at: "
        + ", ".join(str(path) for path in candidates)
    )


def extract_species_keys(extract_path: Path) -> set[str]:
    if not extract_path.is_file():
        return set()
    payload = yaml.safe_load(extract_path.read_text(encoding="utf-8"))
    species = (payload or {}).get("species") or {}
    return set(species)


def canonical_extract_key(name: str, phase_flag: int | None) -> str:
    if phase_flag == 0:
        return name
    return name.replace("(", "_").replace(")", "").replace(",", "_")


def records_missing_from_extract(
    parsed: ThermoInpFile, extract_keys: set[str]
) -> list[dict[str, str]]:
    missing: list[dict[str, str]] = []
    for rec in parsed.records:
        keys = {
            rec.name_as_published,
            canonical_extract_key(rec.name_as_published, rec.phase_flag),
        }
        if keys.isdisjoint(extract_keys):
            missing.append(
                {
                    "record_id": rec.record_id,
                    "name_as_published": rec.name_as_published,
                    "phase": rec.phase,
                    "cea_section": rec.cea_section,
                    "formula": rec.formula,
                }
            )
    return missing


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def build_manifest(
    parsed: ThermoInpFile,
    *,
    output_root: Path,
    source_sha256: str,
    missing_from_extract: list[dict[str, str]],
    coverage: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    by_formula: dict[str, list[str]] = defaultdict(list)
    total_coeff = 0
    total_amb = 0
    for rec in parsed.records:
        rel = f"data/literature/compilations/nasa-glenn/records/{rec.record_id}.json"
        total_coeff += rec.coefficient_count
        total_amb += len(rec.ambiguities)
        by_formula[rec.formula].append(rec.record_id)
        entry = {
            "record_id": rec.record_id,
            "formula": rec.formula,
            "phase": rec.phase,
            "name_as_published": rec.name_as_published,
            "source": {
                **COMPILATION_SOURCE,
                "official_url": CEA_DOWNLOAD_URL,
                "sha256": source_sha256,
                "path": SOURCE_REL,
            },
            "original_record_text_location": {
                "path": SOURCE_REL,
                "name_line": rec.name_line_number,
                "header_line": rec.header_line_number,
                "end_line": rec.end_line_number,
                "cea_section": rec.cea_section,
            },
            "coefficient_count": rec.coefficient_count,
            "interval_count": rec.interval_count,
            "n_intervals_declared": rec.n_intervals_declared,
            "ambiguity_count": len(rec.ambiguities),
            "ambiguities": list(rec.ambiguities),
            "sha256_source_file": source_sha256,
            "path": rel,
        }
        if rec.phase_ordinal is not None:
            entry["phase_ordinal"] = rec.phase_ordinal
        entries.append(entry)
    covered = [el for el, row in coverage.items() if row["has_record"]]
    uncovered = [el for el, row in coverage.items() if not row["has_record"]]
    return {
        "schema_version": MANIFEST_SCHEMA,
        "source_id": SOURCE_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "compilation_role": dict(COMPILATION_ROLE),
        "source": {
            **COMPILATION_SOURCE,
            "official_url": CEA_DOWNLOAD_URL,
            "path": SOURCE_REL,
            "sha256": source_sha256,
            "record_format": (
                "CEA fixed-column thermo.inp: name line (cols 1-18); "
                "header I2,1X,A6,1X,5(A2,F6.2),I2,F13.7,F15.3; "
                "per interval 2F11.3,I1,8F5.1,2X,F15.3 plus two 5D16 lines "
                "(7 a-coefficients + unused a8 field + b1 + b2)."
            ),
        },
        "corpus_status": {
            "scope": "complete thermo.inp ingest (products + reactants)",
            "default_t_line_as_published": parsed.default_t_line,
            "default_t_line_number": parsed.default_t_line_number,
            "products_end_marker": "END PRODUCTS",
            "reactants_end_marker": "END REACTANTS",
            "held_extract_superseded": "data/literature/extracts/nasa-cea-thermo.yaml",
            "held_extract_species_count": 1615,
            "held_extract_missing_record_count": len(missing_from_extract),
        },
        "summary": {
            "record_count": len(parsed.records),
            "product_record_count": sum(
                1 for rec in parsed.records if rec.cea_section == "products"
            ),
            "reactant_record_count": sum(
                1 for rec in parsed.records if rec.cea_section == "reactants"
            ),
            "coefficient_count": total_coeff,
            "parse_ambiguity_count": total_amb,
            "records_with_ambiguity_count": sum(
                1 for rec in parsed.records if rec.ambiguities
            ),
            "formula_count": len(by_formula),
            "source_schema_version": SCHEMA_VERSION,
        },
        "feedstock_element_coverage": {
            "covered_elements": covered,
            "uncovered_elements": uncovered,
            "per_element": coverage,
        },
        "held_extract_missing_records": missing_from_extract,
        "entries": entries,
    }


def main() -> int:
    args = cli()
    output_root = args.output_root.resolve()
    source_dir = output_root / "source"
    records_dir = output_root / "records"
    source_dir.mkdir(parents=True, exist_ok=True)
    records_dir.mkdir(parents=True, exist_ok=True)

    thermo_src = resolve_thermo(args.thermo, output_root)
    dest_thermo = source_dir / "thermo.inp"
    if thermo_src.resolve() != dest_thermo.resolve():
        shutil.copyfile(thermo_src, dest_thermo)
    payload = dest_thermo.read_bytes()
    digest = sha256_bytes(payload)
    parsed = parse_thermo_inp(
        payload.decode("latin-1"),
        source_path=SOURCE_REL,
        source_sha256=digest,
    )
    # Clear previous record files so a rerun cannot leave orphans.
    for stale in records_dir.glob("NG-*.json"):
        stale.unlink()
    for rec in parsed.records:
        write_json(records_dir / f"{rec.record_id}.json", record_document(rec))

    extract_keys = extract_species_keys(args.extract)
    missing = records_missing_from_extract(parsed, extract_keys)
    elements = feedstock_element_symbols()
    coverage = coverage_by_element(parsed.records, elements)
    manifest = build_manifest(
        parsed,
        output_root=output_root,
        source_sha256=digest,
        missing_from_extract=missing,
        coverage=coverage,
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
        f"products={manifest['summary']['product_record_count']} "
        f"reactants={manifest['summary']['reactant_record_count']} "
        f"ambiguities={manifest['summary']['parse_ambiguity_count']} "
        f"extract_missing={len(missing)} "
        f"sha256={digest} "
        f"manifest={manifest_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
