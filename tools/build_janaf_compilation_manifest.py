#!/usr/bin/env python3
"""Validate harvested JANAF YAML tables and build their local manifest."""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.reference_data.janaf import (  # noqa: E402
    COMPILATION_ROLE,
    COMPILATION_SOURCE,
    FORMULA_NORMALISED_RULE,
    NON_STOICHIOMETRIC_TABLES,
    PINNED_JANAF_ABSENT_ELEMENTS,
    coverage_by_element,
    feedstock_element_symbols,
    formula_composition,
    formula_normalised,
    harvest_era,
    load_table_document,
    non_stoich_ambiguity,
    table_formula_as_published,
)

JANAF_ROOT = ROOT / "data" / "literature" / "compilations" / "janaf"
TABLES = JANAF_ROOT / "tables"
MANIFEST = JANAF_ROOT / "manifest.yaml"
TARGET_FORMULAS = (
    "Na", "K", "Fe", "Mg", "Si", "SiO", "SiO2", "Ca", "Al", "Cr", "Mn", "Ti",
    "O", "O2", "P", "S", "Cl", "F", "H2O", "CO", "CO2", "Ni", "Zn", "Na2O",
    "NaO", "K2O", "KO", "FeO", "Fe2O3", "MgO", "CaO", "Al2O3", "TiO2", "Cr2O3",
    "MnO", "P2O5", "PO", "PO2",
)


def table_formula(table: dict[str, Any]) -> str:
    return table_formula_as_published(table)


def table_phase(table: dict[str, Any]) -> str:
    index_state = (table.get("index_entry") or {}).get("state")
    if index_state:
        return str(index_state)
    title = str(table.get("title_as_published") or "")
    matches = re.findall(r"\((ref|cr|l|cr,l|g|l,g|fl)\)", title)
    return matches[-1] if matches else "not_parsed"


def main() -> int:
    entries: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    by_formula: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_composition: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    row_count = 0
    ambiguity_count = 0
    html_era = 0
    txt_era = 0
    for path in sorted(TABLES.glob("*.yaml")):
        document = load_table_document(path)
        documents.append(document)
        if document.get("schema_version") != "literature_compilation.v1":
            raise ValueError(f"{path}: wrong schema_version")
        role = document.get("compilation_role") or {}
        if role.get("scoring_eligible") is not False or role.get("validation_measurement") is not False:
            raise ValueError(f"{path}: compilation incorrectly permits validation/scoring")
        table = document.get("table") or {}
        table_id = str(table.get("table_id") or "")
        url = str(table.get("url") or "")
        if not table_id or url != f"https://janaf.nist.gov/tables/{table_id}.html":
            raise ValueError(f"{path}: invalid table identity")
        rows = table.get("values") or []
        for row_number, row in enumerate(rows):
            if len(row) != 8:
                raise ValueError(f"{path}: row {row_number} has {len(row)} properties")
            for property_name, value in row.items():
                locator = value.get("locator") or {}
                if locator.get("table_id") != table_id or locator.get("url") != url:
                    raise ValueError(f"{path}: {property_name} missing exact table locator")
                if "as_published" not in value:
                    raise ValueError(f"{path}: {property_name} missing published token")
        published = table_formula(table)
        normalised = formula_normalised(published)
        phase = table_phase(table)
        ambiguities = list(table.get("parse_ambiguities") or [])
        era = harvest_era(document)
        if era == "html":
            html_era += 1
        elif era == "txt":
            txt_era += 1
        source_sha = (document.get("extraction") or {}).get("source_sha256")
        composition = formula_composition(published)
        entry = {
            "table_id": table_id,
            "formula": published,
            "formula_as_published": published,
            "formula_normalised": normalised,
            "charge": (table.get("index_entry") or {}).get("charge"),
            "phase": phase,
            "title_as_published": table.get("title_as_published"),
            "url": url,
            "download_url": table.get("download_url"),
            "row_count": len(rows),
            "ambiguity_count": len(ambiguities),
            "parse_ambiguities": ambiguities,
            "harvest_era": era,
            "path": path.relative_to(ROOT).as_posix(),
        }
        if source_sha:
            entry["source_sha256"] = source_sha
        entries.append(entry)
        by_formula[published].append(entry)
        if composition is not None:
            by_composition[composition].append(entry)
        row_count += len(rows)
        ambiguity_count += len(ambiguities)
    coverage = {}
    for formula in TARGET_FORMULAS:
        composition = formula_composition(formula)
        matches = by_composition.get(composition, []) if composition is not None else []
        coverage[formula] = {
            "janaf_table_available": bool(matches),
            "table_count": len(matches),
            "phases": sorted({entry["phase"] for entry in matches}),
            "table_ids": [entry["table_id"] for entry in matches],
        }
    feedstock_elements = feedstock_element_symbols()
    feedstock_coverage = coverage_by_element(documents, feedstock_elements)
    missing = [element for element, row in feedstock_coverage.items() if not row["has_record"]]
    non_stoich = []
    for table_id, meta in NON_STOICHIOMETRIC_TABLES.items():
        non_stoich.append(
            {
                "table_id": table_id,
                **non_stoich_ambiguity(table_id),
                "previous_integerised_formula": meta["previous_integerised_formula"],
            }
        )
    harvest_refusals = []
    run_path = JANAF_ROOT / "source-cache" / "harvest-run.yaml"
    if run_path.is_file():
        run = load_table_document(run_path)
        harvest_refusals = [
            {"table_id": entry["table_id"], "reason": entry["error"]}
            for entry in run.get("entries", []) if entry.get("status") == "failed"
        ]
    manifest = {
        "schema_version": "literature_compilation_manifest.v1",
        "source_id": "nist-janaf-4th",
        "source": dict(COMPILATION_SOURCE),
        "corpus_status": {
            "scope": "feedstock-element complete harvest",
            "full_formula_index_status": (
                "harvested every official-index table whose formula uses only "
                "the feedstock element set; available hash-verified .txt sources "
                "replace HTML-era loadable records; unavailable sources are explicit ambiguities"
            ),
            "official_formula_index_url": "https://janaf.nist.gov/formula.html",
            "official_formula_index_total_lines_observed": 1796,
            "html_era_table_count": html_era,
            "txt_era_table_count": txt_era,
        },
        "compilation_role": dict(COMPILATION_ROLE),
        "formula_normalised_rule": FORMULA_NORMALISED_RULE,
        "summary": {
            "table_count": len(entries),
            "thermodynamic_row_count": row_count,
            "parse_ambiguity_count": ambiguity_count + len(harvest_refusals),
            "formula_count": len(by_formula),
        },
        "non_stoichiometric_formulas": non_stoich,
        "parse_ambiguities": harvest_refusals,
        "feedstock_element_coverage": {
            "elements": feedstock_elements,
            "covered_elements": [
                element for element, row in feedstock_coverage.items() if row["has_record"]
            ],
            "uncovered_elements": missing,
            "janaf_absent_elements": list(PINNED_JANAF_ABSENT_ELEMENTS),
            "by_element": feedstock_coverage,
        },
        "target_coverage": coverage,
        "entries": entries,
    }
    MANIFEST.write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True, width=120),
        encoding="utf-8",
    )
    print(
        f"tables={len(entries)} rows={row_count} formulas={len(by_formula)} "
        f"ambiguities={ambiguity_count} manifest={MANIFEST}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
