#!/usr/bin/env python3
"""Validate harvested JANAF YAML tables and build their local manifest."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
JANAF_ROOT = ROOT / "data" / "literature" / "compilations" / "janaf"
TABLES = JANAF_ROOT / "tables"
MANIFEST = JANAF_ROOT / "manifest.yaml"
TARGET_FORMULAS = (
    "Na", "K", "Fe", "Mg", "Si", "SiO", "SiO2", "Ca", "Al", "Cr", "Mn", "Ti",
    "O", "O2", "P", "S", "Cl", "F", "H2O", "CO", "CO2", "Ni", "Zn", "Na2O",
    "NaO", "K2O", "KO", "FeO", "Fe2O3", "MgO", "CaO", "Al2O3", "TiO2", "Cr2O3",
    "MnO", "P2O5", "PO", "PO2",
)


def plain_formula(value: str) -> str:
    value = re.sub(r"_\{([^}]*)\}", r"\1", value)
    value = value.replace("{", "").replace("}", "").replace(" ", "")
    return re.sub(r"([A-Z][a-z]?)1(?=[A-Z]|$)", r"\1", value)


def formula_composition(value: str) -> tuple[tuple[str, int], ...]:
    tokens = re.findall(r"([A-Z][a-z]?)(\d*)", value)
    if not tokens or "".join(element + count for element, count in tokens) != value:
        raise ValueError(f"cannot parse formula {value!r}")
    counts: dict[str, int] = defaultdict(int)
    for element, count in tokens:
        counts[element] += int(count or "1")
    return tuple(sorted(counts.items()))


def table_formula(table: dict[str, Any]) -> str:
    index_formula = (table.get("index_entry") or {}).get("formula")
    if index_formula:
        return str(index_formula)
    title = str(table.get("title_as_published") or "")
    segments = [segment.strip() for segment in title.split("|") if segment.strip()]
    if len(segments) >= 2:
        candidate = re.sub(r"\((?:ref|cr|l|cr,l|g|l,g|fl)\)$", "", segments[-1])
        formula = plain_formula(candidate)
        if re.fullmatch(r"(?:[A-Z][a-z]?\d*)+", formula):
            return formula
    for candidate in re.findall(r"\(([^()]*)\)", title):
        formula = plain_formula(candidate)
        if re.fullmatch(r"(?:[A-Z][a-z]?\d*)+(?:[+-])?", formula):
            return formula
    raise ValueError(f"cannot identify formula from title {title!r}")


def table_phase(table: dict[str, Any]) -> str:
    index_state = (table.get("index_entry") or {}).get("state")
    if index_state:
        return str(index_state)
    title = str(table.get("title_as_published") or "")
    matches = re.findall(r"\((ref|cr|l|cr,l|g|l,g|fl)\)", title)
    return matches[-1] if matches else "not_parsed"


def main() -> int:
    entries: list[dict[str, Any]] = []
    by_formula: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_composition: dict[tuple[tuple[str, int], ...], list[dict[str, Any]]] = defaultdict(list)
    row_count = 0
    ambiguity_count = 0
    for path in sorted(TABLES.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
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
        formula = table_formula(table)
        phase = table_phase(table)
        ambiguities = table.get("parse_ambiguities") or []
        entry = {
            "table_id": table_id,
            "formula": formula,
            "phase": phase,
            "title_as_published": table.get("title_as_published"),
            "url": url,
            "download_url": table.get("download_url"),
            "row_count": len(rows),
            "ambiguity_count": len(ambiguities),
            "path": path.relative_to(ROOT).as_posix(),
        }
        entries.append(entry)
        by_formula[formula].append(entry)
        by_composition[formula_composition(formula)].append(entry)
        row_count += len(rows)
        ambiguity_count += len(ambiguities)
    coverage = {}
    for formula in TARGET_FORMULAS:
        matches = by_composition.get(formula_composition(formula), [])
        coverage[formula] = {
            "janaf_table_available": bool(matches),
            "table_count": len(matches),
            "phases": sorted({entry["phase"] for entry in matches}),
            "table_ids": [entry["table_id"] for entry in matches],
        }
    manifest = {
        "schema_version": "literature_compilation_manifest.v1",
        "source_id": "nist-janaf-4th",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus_status": {
            "scope": "target-first partial harvest",
            "full_formula_index_status": "harvester implemented; full run blocked by managed worker network isolation",
            "official_formula_index_url": "https://janaf.nist.gov/formula.html",
            "official_formula_index_total_lines_observed": 1800,
        },
        "compilation_role": {
            "engine_reference_input": True,
            "validation_measurement": False,
            "scoring_eligible": False,
            "battery_refusal": "gibbs_table_not_runtime_observable",
        },
        "summary": {
            "table_count": len(entries),
            "thermodynamic_row_count": row_count,
            "parse_ambiguity_count": ambiguity_count,
            "formula_count": len(by_formula),
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
