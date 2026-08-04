#!/usr/bin/env python3
"""MC-2: full CEA thermo.inp → literature extract store (nasa-cea-thermo.yaml).

Drives ``tools/vp_cea_ingest.parse_thermo_inp`` (no second parser). Appends
NEW species only so the pre-existing 157 entries stay byte-stable in the
YAML file. Writes a machine-readable skip/count report alongside.

Usage::

  python tools/mc2_cea_full_ingest.py
  python tools/mc2_cea_full_ingest.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.vp_cea_ingest import (  # noqa: E402
    BulkSkipReport,
    CeaSpeciesRecord,
    _canonical_gas_id,
    _formula_from_tokens,
    _merge_same_name_records,
    _ref_pressure_fields,
    parse_thermo_inp,
)

THERMO_PATH = (
    ROOT
    / "docs-private"
    / "research"
    / "2026-08-01-cea-sweep"
    / "thermo.inp"
)
EXTRACT_PATH = ROOT / "data" / "literature" / "extracts" / "nasa-cea-thermo.yaml"
CARRIERS_PATH = (
    ROOT
    / "docs-private"
    / "research"
    / "2026-08-04-discovery-sweep"
    / "lane-b-carriers.json"
)
REPORT_PATH = (
    ROOT
    / "docs-private"
    / "research"
    / "2026-08-04-mc2-ingest"
    / "ingest-report.json"
)

NOBLE_ELEMENTS = frozenset({"He", "Ne", "Ar", "Kr", "Xe", "Rn"})
SOURCE_PATH_REL = "docs-private/research/2026-08-01-cea-sweep/thermo.inp"


def _normalize_el(token: str) -> str:
    """CEA formula tokens → IUPAC element symbol (AL→Al, CL→Cl)."""
    raw = str(token).strip()
    if not raw or not raw.isalpha():
        return raw
    upper = raw.upper()
    if upper == "AL":
        return "Al"
    if upper == "CL":
        return "Cl"
    if upper == "E":
        return "E"
    return raw[0].upper() + raw[1:].lower()


def formula_elements(rec: CeaSpeciesRecord) -> list[str]:
    els: list[str] = []
    toks = rec.formula_tokens
    i = 0
    while i < len(toks):
        t = toks[i]
        if re.fullmatch(r"[A-Za-z]{1,2}", str(t)):
            els.append(_normalize_el(str(t)))
            i += 2 if i + 1 < len(toks) else 1
        else:
            i += 1
    return els


def is_ion(rec: CeaSpeciesRecord) -> bool:
    if any(str(t).upper() == "E" for t in rec.formula_tokens):
        return True
    name = rec.name.strip()
    return bool(re.search(r"[+-]+\s*$", name))


def is_isotope(rec: CeaSpeciesRecord) -> bool:
    for t in rec.formula_tokens:
        if re.fullmatch(r"[A-Za-z]{1,2}", str(t)) and str(t).upper() in ("D", "T"):
            return True
    return False


def is_noble_species(rec: CeaSpeciesRecord) -> bool:
    chem = [e for e in formula_elements(rec) if e != "E"]
    if not chem:
        return False
    return all(e in NOBLE_ELEMENTS for e in chem)


def species_key(rec: CeaSpeciesRecord) -> str:
    """Canonical extract species id (matches existing 157 conventions)."""
    if int(rec.phase_flag) == 0 or rec.standard_state == "gas":
        # Gas: CEA name verbatim (AL, CaCL2, HCL, …).
        return rec.name
    # Condensed: Fe2O3(cr) → Fe2O3_cr, C(gr) → C_gr
    return _canonical_gas_id(rec.name)


def load_feedstock_elements() -> set[str]:
    """Prefer lane-B census flag; fall back to oxides in feedstocks.yaml."""
    feed: set[str] = set()
    if CARRIERS_PATH.is_file():
        data = json.loads(CARRIERS_PATH.read_text(encoding="utf-8"))
        for el, info in (data.get("elements") or {}).items():
            if info.get("in_feedstock_definitions"):
                feed.add(str(el))
    if feed:
        return feed
    # Fallback: derive from data/feedstocks.yaml oxide keys + REE set.
    fs_path = ROOT / "data" / "feedstocks.yaml"
    fs = yaml.safe_load(fs_path.read_text(encoding="utf-8"))
    oxide_els: set[str] = set()
    for entry in fs.values() if isinstance(fs, dict) else []:
        if not isinstance(entry, dict):
            continue
        for ox in (entry.get("composition_wt_pct") or {}):
            m = re.match(r"([A-Z][a-z]?)", str(ox))
            if m:
                oxide_els.add(m.group(1))
    oxide_els.update(
        {
            "O",
            "H",
            "C",
            "N",
            "S",
            "Cl",
            "P",
            "Ce",
            "Dy",
            "Er",
            "Eu",
            "Gd",
            "Ho",
            "La",
            "Lu",
            "Nd",
            "Pm",
            "Pr",
            "Sm",
            "Tb",
            "Tm",
            "Y",
            "Yb",
            "Th",
        }
    )
    return oxide_els


def record_to_observation(rec: CeaSpeciesRecord) -> dict[str, Any]:
    """Literature-extract observation matching migrate_pilot_extracts CEA shape."""
    ref_pa, _ref_conv = _ref_pressure_fields(rec)
    tmin = float(rec.intervals[0]["T_min_K"])
    tmax = float(rec.intervals[-1]["T_max_K"])
    cea_name = rec.name
    phase = rec.standard_state  # gas | condensed_solid | condensed_liquid | condensed
    return {
        "observation_id": f"cea_{cea_name}_gibbs",
        "type": "gibbs_table",
        "locator": {
            "source_path": SOURCE_PATH_REL,
            "record": str(cea_name),
            "note": (
                "NASA CEA thermo.inp coefficient record; segments preserved "
                "verbatim via tools/vp_cea_ingest.parse_thermo_inp (MC-2 full ingest)"
            ),
        },
        "T_range_K": [tmin, tmax],
        "phase": phase,
        "standard_state": (
            f"CEA/JANAF P°={ref_pa} Pa; phase_flag={rec.phase_flag}"
        ),
        "regime": "gas_standard_state_thermo",
        "units": "NASA CEA polynomial (Cp/R, H/RT, S/R); delta_f_H in J/mol",
        "uncertainty": {
            "note": "Source evaluation uncertainties not restated in thermo.inp rows"
        },
        "values": {
            "cea_name": cea_name,
            "evaluator_family": "nasa_cea_9",
            "formula": _formula_from_tokens(rec.formula_tokens),
            "molecular_weight_g_per_mol": rec.molecular_weight_g_per_mol,
            "delta_f_H_298_15_J_per_mol": rec.delta_f_H_298_15_J_per_mol,
            "source_ref_code": rec.source_ref_code,
            "citation": rec.citation,
            "reference_pressure_Pa": ref_pa,
            "segments": [
                {
                    "T_min_K": iv["T_min_K"],
                    "T_max_K": iv["T_max_K"],
                    "exponents": iv["exponents"],
                    "a_coefficients": iv["a_coefficients"],
                    "b1": iv["b1"],
                    "b2": iv["b2"],
                }
                for iv in rec.intervals
            ],
        },
    }


def select_records(
    records: list[CeaSpeciesRecord],
    *,
    feedstock_elements: set[str],
) -> tuple[list[CeaSpeciesRecord], Counter, list[dict[str, Any]]]:
    reasons: Counter = Counter()
    selected: list[CeaSpeciesRecord] = []
    skip_detail: list[dict[str, Any]] = []
    for rec in records:
        if is_ion(rec):
            reasons["ion"] += 1
            skip_detail.append(
                {"cea_name": rec.name, "reason": "ion", "phase_flag": rec.phase_flag}
            )
            continue
        if is_isotope(rec):
            reasons["isotope_D_or_T"] += 1
            skip_detail.append(
                {
                    "cea_name": rec.name,
                    "reason": "isotope_D_or_T",
                    "phase_flag": rec.phase_flag,
                }
            )
            continue
        if is_noble_species(rec):
            reasons["noble_gas"] += 1
            skip_detail.append(
                {
                    "cea_name": rec.name,
                    "reason": "noble_gas",
                    "phase_flag": rec.phase_flag,
                }
            )
            continue
        if int(rec.phase_flag) == 0:
            selected.append(rec)
            reasons["gas_selected"] += 1
            continue
        els = formula_elements(rec)
        if any(e in feedstock_elements for e in els):
            selected.append(rec)
            reasons["condensed_feedstock"] += 1
        else:
            reasons["condensed_non_feedstock"] += 1
            skip_detail.append(
                {
                    "cea_name": rec.name,
                    "reason": "condensed_non_feedstock",
                    "phase_flag": rec.phase_flag,
                    "elements": els,
                }
            )
    return selected, reasons, skip_detail


def _yaml_species_block(key: str, obs: dict[str, Any]) -> str:
    """Dump one species block with root-level key, matching extract indent."""
    doc = {key: {"observations": [obs]}}
    text = yaml.safe_dump(
        doc,
        sort_keys=False,
        allow_unicode=True,
        width=100,
        default_flow_style=False,
    )
    # Indent every line by 2 spaces so it sits under `species:`
    return "".join("  " + line if line.strip() else line for line in text.splitlines(True))


def append_species_byte_stable(
    extract_path: Path,
    new_species: dict[str, dict[str, Any]],
    *,
    extraction_note: str,
) -> None:
    """Insert new species YAML before fidelity_samples; leave existing bytes intact."""
    text = extract_path.read_text(encoding="utf-8")
    marker = "\nfidelity_samples:"
    if marker not in text and not text.endswith("fidelity_samples:"):
        # tolerate EOF form
        if "\nfidelity_samples:\n" not in text and "fidelity_samples:" not in text:
            raise RuntimeError("fidelity_samples: marker not found in extract")
        idx = text.rfind("fidelity_samples:")
        if idx < 0:
            raise RuntimeError("fidelity_samples: marker not found")
        # include preceding newline if present
        if idx > 0 and text[idx - 1] == "\n":
            head = text[: idx - 1]
            tail = text[idx - 1 :]
        else:
            head = text[:idx]
            tail = text[idx:]
    else:
        # Prefer split on leading-newline form for clean insert.
        parts = text.rsplit(marker, 1)
        if len(parts) != 2:
            raise RuntimeError("failed to split on fidelity_samples")
        head, rest = parts
        tail = marker + rest

    # Stable order: sort new keys (ASCII) for reproducibility.
    blocks: list[str] = []
    for key in sorted(new_species.keys(), key=lambda s: s.encode("utf-8")):
        obs = new_species[key]["observations"][0]
        blocks.append(_yaml_species_block(key, obs))
    insert = "".join(blocks)
    if not head.endswith("\n"):
        head = head + "\n"
    new_text = head + insert + (tail if tail.startswith("\n") else "\n" + tail)

    # Light metadata touch: extraction method/date/worker — only if present as
    # single-line keys so we don't reformat the body.
    new_text = re.sub(
        r"(?m)^(extraction:\n(?:  .*\n)*?  method: ).*$",
        r"\1tools/vp_cea_ingest.py + tools/mc2_cea_full_ingest.py from thermo.inp; "
        r"MC-2 full gas+feedstock-condensed ingest",
        new_text,
        count=1,
    )
    new_text = re.sub(
        r"(?m)^(  date: ).*$",
        rf"\1'{date.today().isoformat()}'",
        new_text,
        count=1,
    )
    new_text = re.sub(
        r"(?m)^(  worker: ).*$",
        r"\1mc2-ingest-gk",
        new_text,
        count=1,
    )
    # Ensure method note doesn't get lost if regex failed quietly — optional.
    _ = extraction_note
    extract_path.write_text(new_text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--thermo", type=Path, default=THERMO_PATH)
    ap.add_argument("--extract", type=Path, default=EXTRACT_PATH)
    args = ap.parse_args(argv)

    if not args.thermo.is_file():
        print(f"ERROR: thermo.inp missing at {args.thermo}", file=sys.stderr)
        return 2

    feed = load_feedstock_elements()
    raw = args.thermo.read_text(encoding="utf-8", errors="replace").replace(
        "\r\n", "\n"
    )
    skip_report = BulkSkipReport()
    records = parse_thermo_inp(
        raw, skip_invalid_segments=True, skip_report=skip_report
    )
    selected, reasons, skip_detail = select_records(
        records, feedstock_elements=feed
    )
    merged = _merge_same_name_records(selected)

    existing_doc = yaml.safe_load(args.extract.read_text(encoding="utf-8"))
    existing_keys = set(existing_doc.get("species") or {})
    before_n = len(existing_keys)

    new_species: dict[str, dict[str, Any]] = {}
    key_map: list[dict[str, Any]] = []
    for rec in merged:
        key = species_key(rec)
        key_map.append(
            {
                "species_id": key,
                "cea_name": rec.name,
                "phase": rec.standard_state,
                "phase_flag": rec.phase_flag,
                "formula": _formula_from_tokens(rec.formula_tokens),
                "already_in_store": key in existing_keys,
            }
        )
        if key in existing_keys:
            continue
        if key in new_species:
            raise RuntimeError(f"duplicate new species key {key!r}")
        new_species[key] = {"observations": [record_to_observation(rec)]}

    report = {
        "thermo_path": str(args.thermo.relative_to(ROOT)),
        "parsed_products_records": len(records),
        "parser_skipped_species": skip_report.skipped_species,
        "parser_dropped_inverted_segments": skip_report.dropped_inverted_segments,
        "filter_reasons": dict(reasons),
        "selected_before_merge": len(selected),
        "selected_after_merge": len(merged),
        "store_before": before_n,
        "new_species_count": len(new_species),
        "store_after_expected": before_n + len(new_species),
        "existing_preserved": before_n,
        "feedstock_elements": sorted(feed),
        "skip_detail_counts": dict(Counter(s["reason"] for s in skip_detail)),
        "skip_detail": skip_detail,
        "key_map": key_map,
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    print(
        f"parsed={len(records)} selected={len(merged)} "
        f"new={len(new_species)} store {before_n}→{before_n + len(new_species)}"
    )
    print(f"filter: {dict(reasons)}")
    print(f"wrote report {REPORT_PATH.relative_to(ROOT)}")

    if args.dry_run:
        print("dry-run: extract not modified")
        return 0

    # Snapshot existing species for post-check
    existing_species_snapshot = {
        k: existing_doc["species"][k] for k in existing_keys
    }

    append_species_byte_stable(
        args.extract,
        new_species,
        extraction_note="MC-2 full CEA ingest",
    )

    # Semantic proof: existing 157 observations unchanged.
    after = yaml.safe_load(args.extract.read_text(encoding="utf-8"))
    after_keys = set(after["species"])
    missing = existing_keys - after_keys
    if missing:
        raise RuntimeError(f"existing keys vanished: {sorted(missing)[:20]}")
    for k in existing_keys:
        if after["species"][k] != existing_species_snapshot[k]:
            raise RuntimeError(f"existing species {k!r} mutated after ingest")
    print(
        f"OK: {len(existing_keys)} pre-existing species semantically unchanged; "
        f"store now {len(after_keys)} species"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
