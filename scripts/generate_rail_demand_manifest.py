#!/usr/bin/env python3
"""Generate the multi-carrier vapour-rail demand manifest.

Demand is the cross-reference of declared feedstock elements with neutral gas
carriers found in the checked-in thermo/KEMS extracts and current catalogue.
The fuller Lane-B CEA census is an optional, repo-relative input: when it is
present its neutral carriers join the same deterministic union.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.vapour_rail.u0_manifest import canonicalize_gas_id  # noqa: E402

DEFAULT_OUTPUT = ROOT / "data" / "vapour_rail_demand_manifest.yaml"
DEFAULT_FEEDSTOCKS = ROOT / "data" / "feedstocks.yaml"
DEFAULT_CEA_EXTRACT = ROOT / "data" / "literature" / "extracts" / "nasa-cea-thermo.yaml"
DEFAULT_JANAF_EXTRACT = ROOT / "data" / "literature" / "extracts" / "janaf-4th.yaml"
DEFAULT_EXTRACT_DIR = ROOT / "data" / "literature" / "extracts"
DEFAULT_CATALOG = ROOT / "data" / "vapor_pressures.yaml"
DEFAULT_LANE_B: Path | None = None

_PHASE_SUFFIX = re.compile(r"\((?:g|gas|l|liq|s|cr|solid)\)$", re.IGNORECASE)
_FORMULA_TOKEN = re.compile(r"([A-Z][a-z]?)(\d+(?:\.\d+)?|\.\d+)?")
_KEMS_CARRIER_FIELDS = frozenset(
    {
        "gas_species",
        "gas_species_observed",
        "also_observed",
    }
)


def _load_yaml(path: Path) -> Mapping[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path}: expected a YAML mapping")
    return payload


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path)


def _formula_atoms(formula: str) -> dict[str, float] | None:
    cleaned = _PHASE_SUFFIX.sub("", formula.strip())
    if cleaned.endswith("_gas"):
        cleaned = cleaned[:-4]
    cleaned = re.sub(r"[+-]+$", "", cleaned)
    matches = list(_FORMULA_TOKEN.finditer(cleaned))
    if not matches or "".join(match.group(0) for match in matches) != cleaned:
        return None
    atoms: dict[str, float] = {}
    for match in matches:
        element = match.group(1)
        atoms[element] = atoms.get(element, 0.0) + float(match.group(2) or 1.0)
    return atoms


def _canonical_formula(formula: str) -> str | None:
    cleaned = _PHASE_SUFFIX.sub("", formula.strip())
    if cleaned.endswith("_gas"):
        cleaned = cleaned[:-4]
    cleaned = re.sub(r"[+-]+$", "", cleaned)
    return cleaned if _formula_atoms(cleaned) else None


def feedstock_elements(feedstocks_path: Path) -> tuple[str, ...]:
    """Return declared non-oxygen element symbols from loadable compositions."""

    elements: set[str] = set()
    for row in _load_yaml(feedstocks_path).values():
        if not isinstance(row, Mapping):
            continue
        for field in ("composition_wt_pct", "elemental_composition"):
            composition = row.get(field)
            if not isinstance(composition, Mapping):
                continue
            for formula, amount in composition.items():
                try:
                    present = float(amount) > 0.0
                except (TypeError, ValueError):
                    present = False
                atoms = _formula_atoms(str(formula))
                if not present or atoms is None:
                    continue
                elements.update(element for element in atoms if element != "O")
        stage0 = row.get("stage0_formula_inventory")
        if isinstance(stage0, Mapping):
            for entry in stage0.values():
                if not isinstance(entry, Mapping):
                    continue
                atoms = entry.get("atoms")
                if isinstance(atoms, Mapping):
                    elements.update(
                        str(element)
                        for element, count in atoms.items()
                        if str(element) != "O" and float(count) > 0.0
                    )
    return tuple(sorted(elements))


class _DemandCollector:
    def __init__(self, elements: Iterable[str]) -> None:
        self.elements = frozenset(elements)
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}

    def add(
        self,
        *,
        formula: str,
        source: str,
        source_species_id: str,
        carrier_id: str | None = None,
        explicit_elements: Iterable[str] | None = None,
        catalog_family: str | None = None,
        catalog_species_id: str | None = None,
        thermo_available: bool = False,
    ) -> None:
        canonical = _canonical_formula(formula)
        atoms = _formula_atoms(canonical or "")
        if canonical is None or atoms is None:
            return
        non_oxygen_elements = set(atoms) - {"O"}
        if not non_oxygen_elements.issubset(self.elements):
            return
        carrier = carrier_id or canonicalize_gas_id(canonical, treat_as_gas=True)
        owners = set(explicit_elements or atoms).intersection(self.elements)
        for element in sorted(owners):
            key = (element, carrier)
            row = self.rows.setdefault(
                key,
                {
                    "element": element,
                    "carrier": carrier,
                    "formula": canonical,
                    "atoms": {name: atoms[name] for name in sorted(atoms)},
                    "sources": set(),
                    "source_species_ids": set(),
                    "catalog_family_ids": set(),
                    "catalog_species_ids": set(),
                    "thermo_available": False,
                },
            )
            row["sources"].add(source)
            row["source_species_ids"].add(f"{source}:{source_species_id}")
            row["thermo_available"] = bool(row["thermo_available"] or thermo_available)
            if catalog_family:
                row["catalog_family_ids"].add(catalog_family)
            if catalog_species_id:
                row["catalog_species_ids"].add(catalog_species_id)

    def render(self) -> list[dict[str, Any]]:
        rendered: list[dict[str, Any]] = []
        for key in sorted(self.rows):
            row = self.rows[key]
            rendered.append(
                {
                    "element": row["element"],
                    "carrier": row["carrier"],
                    "formula": row["formula"],
                    "atoms": row["atoms"],
                    "sources": sorted(row["sources"]),
                    "source_species_ids": sorted(row["source_species_ids"]),
                    "catalog_family_ids": sorted(row["catalog_family_ids"]),
                    "catalog_species_ids": sorted(row["catalog_species_ids"]),
                    "thermo_available": bool(row["thermo_available"]),
                }
            )
        return rendered


def _extract_species_rows(
    collector: _DemandCollector,
    path: Path,
    *,
    source: str,
    all_species_are_gas: bool = False,
) -> None:
    species = _load_yaml(path).get("species")
    if not isinstance(species, Mapping):
        return
    for source_id, payload in species.items():
        if not isinstance(payload, Mapping):
            continue
        observations = payload.get("observations")
        if not isinstance(observations, list):
            continue
        if all_species_are_gas:
            formula = str(source_id)
            for observation in observations:
                if not isinstance(observation, Mapping):
                    continue
                values = observation.get("values")
                if isinstance(values, Mapping) and values.get("formula"):
                    formula = str(values["formula"])
                    break
            collector.add(
                formula=formula,
                source=source,
                source_species_id=str(source_id),
                thermo_available=True,
            )
            continue
        for observation in observations:
            if not isinstance(observation, Mapping):
                continue
            values = observation.get("values")
            values = values if isinstance(values, Mapping) else {}
            phase = str(observation.get("phase") or values.get("standard_state") or "")
            phase_flag = values.get("phase_flag")
            is_gas = phase_flag == 0 or "gas" in phase.lower()
            if not is_gas:
                continue
            formula = str(values.get("formula") or source_id)
            collector.add(
                formula=formula,
                source=source,
                source_species_id=str(source_id),
                thermo_available=True,
            )


def _carrier_strings(value: Any, *, field: str | None = None) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield from _carrier_strings(child, field=str(key))
        return
    if isinstance(value, list):
        for child in value:
            yield from _carrier_strings(child, field=field)
        return
    if field in _KEMS_CARRIER_FIELDS and isinstance(value, str):
        for token in re.split(r"[,;/]|\s+and\s+", value):
            token = token.strip()
            if token:
                yield token


def _kems_rows(collector: _DemandCollector, extract_dir: Path) -> list[str]:
    consumed: list[str] = []
    for path in sorted(extract_dir.glob("kems-*.yaml")):
        payload = _load_yaml(path)
        species = payload.get("species")
        if not isinstance(species, Mapping):
            continue
        source = str(payload.get("source_id") or path.stem)
        consumed.append(_display_path(path))
        for source_id, row in species.items():
            if not isinstance(row, Mapping):
                continue
            observations = row.get("observations")
            if not isinstance(observations, list):
                continue
            candidates: set[str] = set()
            for observation in observations:
                if not isinstance(observation, Mapping):
                    continue
                regime = str(observation.get("regime") or "").lower()
                if regime != "kems_effusion":
                    continue
                candidates.update(_carrier_strings(observation))
            for candidate in sorted(candidates):
                collector.add(
                    formula=candidate,
                    source=source,
                    source_species_id=str(source_id),
                )
    return consumed


def _catalog_rows(collector: _DemandCollector, path: Path) -> None:
    families = _load_yaml(path).get("families")
    if not isinstance(families, Mapping):
        return
    for family_id, family in families.items():
        if not isinstance(family, Mapping):
            continue
        physical = family.get("physical_properties")
        physical = physical if isinstance(physical, Mapping) else {}
        species = physical.get("species")
        if not isinstance(species, Mapping):
            continue
        for species_id, row in species.items():
            row = row if isinstance(row, Mapping) else {}
            collector.add(
                formula=str(row.get("formula") or species_id),
                source="vapor_pressures_catalog",
                source_species_id=str(species_id),
                carrier_id=str(species_id),
                catalog_family=str(family_id),
                catalog_species_id=str(species_id),
            )


def _lane_b_rows(collector: _DemandCollector, path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "lane-b-carriers.v1":
        raise ValueError(f"{path}: unsupported Lane-B schema {payload.get('schema')!r}")
    elements = payload.get("elements")
    if not isinstance(elements, Mapping):
        raise ValueError(f"{path}: Lane-B elements must be a mapping")
    for element in sorted(collector.elements):
        element_row = elements.get(element)
        if not isinstance(element_row, Mapping):
            continue
        carriers = element_row.get("gas_carriers_neutral")
        if not isinstance(carriers, list):
            continue
        for carrier in carriers:
            if not isinstance(carrier, Mapping) or carrier.get("is_ion"):
                continue
            collector.add(
                formula=str(carrier.get("formula") or carrier.get("cea_name") or ""),
                source="lane_b_full_cea_census",
                source_species_id=str(carrier.get("cea_name") or carrier.get("formula")),
                explicit_elements=(element,),
                thermo_available=True,
            )


def build_manifest(
    *,
    feedstocks_path: Path = DEFAULT_FEEDSTOCKS,
    cea_extract_path: Path = DEFAULT_CEA_EXTRACT,
    janaf_extract_path: Path = DEFAULT_JANAF_EXTRACT,
    extract_dir: Path = DEFAULT_EXTRACT_DIR,
    catalog_path: Path = DEFAULT_CATALOG,
    lane_b_path: Path | None = DEFAULT_LANE_B,
) -> dict[str, Any]:
    elements = feedstock_elements(feedstocks_path)
    collector = _DemandCollector(elements)
    _extract_species_rows(collector, cea_extract_path, source="nasa_cea_extract")
    _extract_species_rows(
        collector,
        janaf_extract_path,
        source="janaf_4th_extract",
        all_species_are_gas=True,
    )
    kems_paths = _kems_rows(collector, extract_dir)
    _catalog_rows(collector, catalog_path)
    lane_b_consumed = lane_b_path is not None
    if lane_b_consumed:
        if not lane_b_path.is_file():
            raise FileNotFoundError(f"explicit Lane-B census not found: {lane_b_path}")
        _lane_b_rows(collector, lane_b_path)
    pairs = collector.render()
    carriers = sorted({row["carrier"] for row in pairs})
    catalog_linked_pairs = sum(bool(row["catalog_species_ids"]) for row in pairs)
    return {
        "schema_version": 1,
        "kind": "vapour_rail_demand_manifest",
        "description": (
            "Generated (element, neutral gas carrier) demand. Oxygen and the lumped "
            "REE_oxides placeholder are not element owners; individual REEs enter when "
            "feedstocks declare resolvable formulas."
        ),
        "counts": {
            "elements": len(elements),
            "carriers": len(carriers),
            "pairs": len(pairs),
            "catalog_linked_pairs": catalog_linked_pairs,
        },
        "provenance": {
            "feedstocks": _display_path(feedstocks_path),
            "nasa_cea_extract": _display_path(cea_extract_path),
            "janaf_4th_extract": _display_path(janaf_extract_path),
            "kems_extracts": kems_paths,
            "catalog": _display_path(catalog_path),
            "lane_b_full_cea_census": {
                "path": _display_path(lane_b_path) if lane_b_path else None,
                "consumed": lane_b_consumed,
                "note": (
                    "Explicit opt-in seam. The default manifest never depends on "
                    "the presence of a machine-local research artifact."
                ),
            },
        },
        "elements": list(elements),
        "carriers": carriers,
        "pairs": pairs,
    }


def render_manifest(document: Mapping[str, Any]) -> str:
    return yaml.safe_dump(
        dict(document), sort_keys=False, allow_unicode=False, width=100
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--feedstocks", type=Path, default=DEFAULT_FEEDSTOCKS)
    parser.add_argument("--cea-extract", type=Path, default=DEFAULT_CEA_EXTRACT)
    parser.add_argument("--janaf-extract", type=Path, default=DEFAULT_JANAF_EXTRACT)
    parser.add_argument("--extract-dir", type=Path, default=DEFAULT_EXTRACT_DIR)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--lane-b-carriers", type=Path, default=DEFAULT_LANE_B)
    parser.add_argument(
        "--check", action="store_true", help="fail if output differs from regeneration"
    )
    args = parser.parse_args(argv)

    document = build_manifest(
        feedstocks_path=args.feedstocks,
        cea_extract_path=args.cea_extract,
        janaf_extract_path=args.janaf_extract,
        extract_dir=args.extract_dir,
        catalog_path=args.catalog,
        lane_b_path=args.lane_b_carriers,
    )
    rendered = render_manifest(document)
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != rendered:
            print(f"STALE: regenerate {args.output}", file=sys.stderr)
            return 1
        print(
            "Rail demand manifest fresh: "
            f"elements={document['counts']['elements']} "
            f"carriers={document['counts']['carriers']} pairs={document['counts']['pairs']}"
        )
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(
        f"Wrote {args.output}: elements={document['counts']['elements']} "
        f"carriers={document['counts']['carriers']} pairs={document['counts']['pairs']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
