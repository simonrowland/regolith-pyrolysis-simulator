#!/usr/bin/env python3
"""Ingest the complete supplied ATcT 1.222 HTML snapshot; load native records."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from collections import Counter
from decimal import Decimal
from pathlib import Path

import yaml

from tools.harvest_janaf_compilation import strip_tags, write_yaml


ROOT = Path(__file__).resolve().parents[1]
ATCT_ROOT = ROOT / "data/literature/compilations/atct"
SOURCE_NAME = "atct-anl-1.222.html"
ROLE = {
    "kind": "assessed_formation_enthalpies",
    "engine_reference_input": True,
    "validation_measurement": False,
    "scoring_eligible": False,
    "battery_refusal": "gibbs_table_not_runtime_observable",
}
ROW_RE = re.compile(rb'<tr id="([^"]+)"[^>]*>(.*?)</tr>', re.DOTALL)
NUMBER_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?")


def text(fragment: str) -> str:
    return strip_tags(fragment).strip()


def numeric_token(token: str) -> Decimal | None:
    """Preserve absent and exact tokens; never manufacture a numeric value."""
    if token in ("", "exact"):
        return None
    number = token.removeprefix("±").strip()
    if not NUMBER_RE.fullmatch(number):
        raise ValueError(f"non-numeric published token: {token!r}")
    return Decimal(number)


def parse_source(payload: bytes) -> list[dict]:
    records = []
    for row_number, match in enumerate(ROW_RE.finditer(payload), 1):
        row_id, body = (part.decode("utf-8") for part in match.groups())
        cells = re.findall(r'<td\b.*?</td>', body, re.DOTALL)
        if len(cells) != 9:
            raise ValueError(f"{row_id}: expected nine native columns")
        sequence = re.search(r"\bi(\d+)\b", row_id).group(1)
        label = text(re.search(r"<button[^>]*>(.*?)</button>", cells[1], re.DOTALL).group(1))
        formula, phase = label.rsplit("  ", 1)
        phase = phase.strip()
        if not (phase.startswith("(") and phase.endswith(")")):
            raise ValueError(f"{row_id}: missing published state")
        values = {}
        for column in ("DHf0", "DHf298", "Uncert", "Units", "Mass", "ATcTID"):
            found = re.search(r'<span class="' + column + r'">(.*?)</span>', body, re.DOTALL)
            if found is None:
                raise ValueError(f"{row_id}: missing {column}")
            values[column] = text(found.group(1))
        for column in ("DHf0", "DHf298", "Uncert"):
            numeric_token(values[column])
        flags = [text(flag) for flag in re.findall(r"<sup\b[^>]*>(.*?)</sup>", body, re.DOTALL)]
        flags += re.findall(r'href="(#[^"]+)"', body)
        ambiguities = []
        if 'class="bkgFormula> <span class="Formula"' in cells[1]:
            ambiguities.append("malformed_formula_cell_attribute; button text retained verbatim")
        for column in ("DHf0", "DHf298", "Units"):
            if not values[column]:
                ambiguities.append(f"{column}_blank_as_published; not inferred")
        name = text(re.search(r'<span class="Name">(.*?)</span>', cells[0], re.DOTALL).group(1))
        if phase not in {"(g)", "(l)", "(cr)", "(aq)", "(graphite)"} or re.search(
            r"\d|[-(),]|\b(?:syn|anti|gauche|ortho|meta|para)\b", name, re.I
        ):
            ambiguities.append("published_isomer_or_state_label_retained; not canonicalized or split")
        if re.search(r"\b(?:rowspan|colspan)\s*=", body):
            ambiguities.append("merged_cells_as_published")
        if flags:
            ambiguities.append("footnote_flags_retained; not resolved")
        records.append({
            "schema_version": "literature_compilation.v1",
            "source_id": "atct-anl-1.222",
            "version": "1.222",
            "compilation_role": ROLE,
            "record_id": f"atct-1.222-{int(sequence):04d}",
            "name_as_published": name,
            "formula": formula,
            "formula_as_published": label,
            "phase": phase[1:-1],
            "cas_as_published": re.search(r"\bCAS(\S+)", row_id).group(1),
            "atct_id": values["ATcTID"],
            "source_locator": {
                "path": f"source/{SOURCE_NAME}",
                "html_row_id": row_id,
                "species_row": row_number,
                "byte_start": match.start(),
                "byte_end": match.end(),
            },
            "formation_enthalpy_0_K_as_published": values["DHf0"],
            "formation_enthalpy_298_15_K_as_published": values["DHf298"],
            "uncertainty_as_published": values["Uncert"],
            "units_as_published": values["Units"],
            "relative_molecular_mass_as_published": values["Mass"],
            "footnote_flags": flags,
            "ambiguities": ambiguities,
        })
    if not records or len({r["record_id"] for r in records}) != len(records):
        raise ValueError("empty or duplicate species rows")
    return records


def load_records(root: Path = ATCT_ROOT):
    """Yield manifest-indexed native records, preserving decimal text and units."""
    manifest = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
    for entry in manifest["entries"]:
        record = json.loads((root / entry["path"]).read_text(encoding="utf-8"))
        if record["record_id"] != entry["record_id"] or record["compilation_role"] != ROLE:
            raise ValueError(f"invalid ATcT record: {entry['path']}")
        for field in ("formation_enthalpy_0_K_as_published", "formation_enthalpy_298_15_K_as_published", "uncertainty_as_published"):
            numeric_token(record[field])
        yield record


def feedstock_coverage(records: list[dict]) -> dict[str, int]:
    from simulator.accounting.formulas import load_species_formulas, resolve_species_formula

    registry = load_species_formulas(ROOT / "data/species_catalog.yaml")
    feedstocks = yaml.safe_load((ROOT / "data/feedstocks.yaml").read_text(encoding="utf-8"))
    elements = set()
    for feedstock in feedstocks.values():
        for species in feedstock.get("composition_wt_pct", {}):
            local = feedstock.get("stage0_formula_inventory", {}).get(species, {})
            formula = local.get("template_formula", species)
            elements.update(resolve_species_formula(formula, registry).elements)
    counts = Counter()
    for record in records:
        tokens = set(re.findall(r"[A-Z][a-z]?", record["formula"]))
        if "D" in tokens:
            tokens.add("H")
        counts.update(tokens & elements)
    return {element: counts[element] for element in sorted(elements)}


def ingest(corpus: Path, output: Path = ATCT_ROOT) -> dict:
    source_dir = output / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    for name in (SOURCE_NAME, "sidecar.yaml"):
        shutil.copyfile(corpus / name, source_dir / name)
    payload = (source_dir / SOURCE_NAME).read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    sidecar = yaml.safe_load((source_dir / "sidecar.yaml").read_text(encoding="utf-8"))
    if digest != sidecar["sha256"]:
        raise ValueError("source digest differs from corpus sidecar")
    records = parse_source(payload)
    source = {
        "database": "Active Thermochemical Tables (ATcT), Argonne National Laboratory",
        "version": "1.222",
        "date_as_published": "07/26/2026",
        "official_url": sidecar["retrieved_url"],
        "retrieved_at": sidecar["retrieved_at"],
        "licence": sidecar["licence"],
        "doi": sidecar["doi"],
    }
    claimed = int(re.search(rb"for all (\d+) species included", payload).group(1))
    corpus_ambiguities = []
    if claimed != len(records):
        corpus_ambiguities.append(f"page claims {claimed} species; supplied HTML contains {len(records)} species rows")
    entries = []
    (output / "records").mkdir(exist_ok=True)
    for record in records:
        record_path = f"records/{record['record_id']}.json"
        (output / record_path).write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        entries.append({
            **{key: record[key] for key in ("record_id", "formula", "phase", "name_as_published", "source_locator", "ambiguities")},
            "source": source,
            "sha256": digest,
            "path": record_path,
            "row_count": 1,
            "ambiguity_count": len(record["ambiguities"]),
        })
    manifest = {
        "schema_version": "literature_compilation_manifest.v1",
        "source_id": "atct-anl-1.222",
        "compilation_role": ROLE,
        "source": source,
        "source_files": [{"path": f"source/{name}", "sha256": hashlib.sha256((source_dir / name).read_bytes()).hexdigest()} for name in (SOURCE_NAME, "sidecar.yaml")],
        "corpus_status": {"scope": "every species row in supplied version 1.222 snapshot", "claimed_species_count": claimed, "ambiguities": corpus_ambiguities},
        "summary": {"record_count": len(records), "record_ambiguity_count": sum(len(r["ambiguities"]) for r in records), "corpus_ambiguity_count": len(corpus_ambiguities)},
        "feedstock_element_coverage": feedstock_coverage(records),
        "entries": entries,
    }
    write_yaml(output / "manifest.yaml", manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    args = parser.parse_args()
    print(json.dumps(ingest(args.corpus)["summary"], sort_keys=True))
