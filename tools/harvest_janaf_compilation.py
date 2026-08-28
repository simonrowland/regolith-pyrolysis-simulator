#!/usr/bin/env python3
"""Harvest the public NIST-JANAF table corpus into traceable YAML files."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import yaml


BASE_URL = "https://janaf.nist.gov/"
INDEX_URL = urllib.parse.urljoin(BASE_URL, "formula.html")
USER_AGENT = (
    "RegolithPyrolysisSimulator-corpus-harvester/1.0 "
    "(public thermochemical reference-data research; polite cached client)"
)
SCHEMA_VERSION = "literature_compilation.v1"
VALUE_COLUMNS = (
    ("temperature", "T/K"),
    ("heat_capacity", "Cp°"),
    ("entropy", "S°"),
    ("negative_gibbs_enthalpy_function", "-[G°-H°(Tr)]/T"),
    ("enthalpy_increment", "H-H°(Tr)"),
    ("formation_enthalpy", "ΔfH°"),
    ("formation_gibbs_energy", "ΔfG°"),
    ("log10_formation_equilibrium_constant", "log Kf"),
)
TARGET_FORMULAS = (
    "Na", "K", "Fe", "Mg", "Si", "SiO", "SiO2", "Ca", "Al", "Cr", "Mn", "Ti",
    "O", "O2", "P", "S", "Cl", "F", "H2O", "CO", "CO2", "Ni", "Zn", "Na2O",
    "NaO", "K2O", "KO", "FeO", "Fe2O3", "MgO", "CaO", "Al2O3", "TiO2", "Cr2O3",
    "MnO", "P2O5", "PO", "PO2",
)
STAGE0_METALS = frozenset({"Na", "K", "Fe", "Mg", "Ca", "Al", "Cr", "Mn", "Ti", "Ni", "Zn"})
STAGE0_ANIONS = frozenset({"Cl", "F", "S"})
NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?$")
FORMULA_TOKEN_RE = re.compile(r"([A-Z][a-z]?)(\d*)")


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href and re.search(r"(?:^|/)tables/[A-Za-z0-9-]+\.html$", href):
            self.links.append(href)


class TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/literature/compilations/janaf"),
    )
    parser.add_argument("--delay-seconds", type=float, default=0.25)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--index-only", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    return parser.parse_args()


def fetch_cached(url: str, cache_path: Path, delay_seconds: float, refresh: bool) -> bytes:
    if cache_path.exists() and not refresh:
        return cache_path.read_bytes()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/plain,text/html"})
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = response.read()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(payload)
            time.sleep(delay_seconds)
            return payload
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code not in {429, 500, 502, 503, 504}:
                break
            time.sleep(max(delay_seconds, 2 ** attempt))
    raise RuntimeError(f"failed to fetch {url}: {last_error}")


def strip_tags(payload: str) -> str:
    parser = TextParser()
    parser.feed(payload)
    return html.unescape("".join(parser.parts))


def composition(formula_label: str) -> dict[str, int] | None:
    formula = re.sub(r"\([^)]*\)$", "", formula_label)
    formula = re.sub(r"[+-]+$", "", formula)
    tokens = FORMULA_TOKEN_RE.findall(formula)
    if not tokens or "".join(element + count for element, count in tokens) != formula:
        return None
    result: dict[str, int] = {}
    for element, count in tokens:
        result[element] = result.get(element, 0) + int(count or "1")
    return result


TARGET_COMPOSITIONS = {json.dumps(composition(formula), sort_keys=True) for formula in TARGET_FORMULAS}


def target_rank(entry: dict[str, Any]) -> int:
    comp = composition(entry["formula_label"])
    if comp is None:
        return 2
    if json.dumps(comp, sort_keys=True) in TARGET_COMPOSITIONS:
        return 0
    elements = frozenset(comp)
    if elements & STAGE0_METALS and elements & STAGE0_ANIONS:
        return 1
    return 2


def parse_index(payload: bytes) -> list[dict[str, Any]]:
    source = payload.decode("utf-8", errors="strict")
    link_parser = LinkParser()
    link_parser.feed(source)
    links = list(dict.fromkeys(link_parser.links))
    text_lines = strip_tags(source).splitlines()
    row_re = re.compile(r"^\s*(\d+)\s+(\S+)\s+(.+?)\s+(\d+)\s*$")
    rows: list[dict[str, Any]] = []
    for line in text_lines:
        match = row_re.match(line)
        if not match:
            continue
        jcode, formula_label, name, page = match.groups()
        rows.append(
            {
                "jcode": int(jcode),
                "formula_label": formula_label,
                "name": name.strip(),
                "fourth_edition_page": int(page),
            }
        )
    if len(rows) != len(links):
        raise ValueError(f"formula index rows/links mismatch: {len(rows)} rows, {len(links)} links")
    entries: list[dict[str, Any]] = []
    for row, href in zip(rows, links, strict=True):
        url = urllib.parse.urljoin(BASE_URL, href)
        table_id = Path(urllib.parse.urlparse(url).path).stem
        row.update(
            {
                "table_id": table_id,
                "url": url,
                "download_url": url.removesuffix(".html") + ".txt",
            }
        )
        entries.append(row)
    return sorted(entries, key=lambda item: (target_rank(item), item["jcode"]))


def scalar_value(token: str) -> float | None:
    cleaned = token.strip()
    if NUMBER_RE.fullmatch(cleaned):
        return float(cleaned)
    return None


def located_value(token: str, entry: dict[str, Any], temperature_token: str, column: str) -> dict[str, Any]:
    return {
        "value": scalar_value(token),
        "as_published": token,
        "locator": {
            "table_id": entry["table_id"],
            "url": entry["url"],
            "download_url": entry["download_url"],
            "row_temperature_as_published": temperature_token,
            "column": column,
        },
    }


def parse_table(payload: bytes, entry: dict[str, Any], cache_path: Path) -> dict[str, Any]:
    source = payload.decode("utf-8", errors="strict").replace("\r\n", "\n")
    lines = source.splitlines()
    header_index = next((i for i, line in enumerate(lines) if line.lstrip().startswith("T/K")), None)
    if header_index is None:
        raise ValueError("missing T/K column header")
    values: list[dict[str, Any]] = []
    ambiguities: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines[header_index + 1 :], start=header_index + 2):
        if not line.strip():
            continue
        fields = [field.strip() for field in line.split("\t")]
        while fields and fields[-1] == "":
            fields.pop()
        if len(fields) == 1:
            fields = line.split()
        if not fields or not NUMBER_RE.fullmatch(fields[0]):
            continue
        if len(fields) != len(VALUE_COLUMNS):
            ambiguities.append(
                {
                    "line_number": line_number,
                    "raw_line": line,
                    "reason": f"expected {len(VALUE_COLUMNS)} tab-separated values; found {len(fields)}",
                    "locator": {"table_id": entry["table_id"], "url": entry["url"]},
                }
            )
            continue
        temperature_token = fields[0]
        row = {
            key: located_value(token, entry, temperature_token, column)
            for (key, column), token in zip(VALUE_COLUMNS, fields, strict=True)
        }
        values.append(row)
    if not values:
        raise ValueError("no unambiguous thermodynamic rows parsed")
    title_lines = [line.strip() for line in lines[:header_index] if line.strip()]
    return {
        "schema_version": SCHEMA_VERSION,
        "source_id": "nist-janaf-4th",
        "source": {
            "citation": (
                "Chase, M. W. Jr., NIST-JANAF Thermochemical Tables, 4th Edition, "
                "J. Phys. Chem. Ref. Data Monograph 9 (1998)"
            ),
            "database": "NIST Standard Reference Database 13",
            "doi": "10.18434/T42S31",
            "url": BASE_URL,
            "last_data_update": 1998,
        },
        "compilation_role": {
            "kind": "assessed_thermodynamic_functions",
            "engine_reference_input": True,
            "validation_measurement": False,
            "scoring_eligible": False,
            "battery_refusal": "gibbs_table_not_runtime_observable",
            "circularity_warning": "Do not validate an engine against a compilation it consumes.",
        },
        "extraction": {
            "method": "machine parse of NIST tab-delimited table download; source bytes cached unchanged",
            "date": date.today().isoformat(),
            "user_agent": USER_AGENT,
            "source_cache_path": cache_path.as_posix(),
            "source_sha256": hashlib.sha256(payload).hexdigest(),
            "review_status": "machine_transcribed_unreviewed",
        },
        "table": {
            **entry,
            "index_target_rank": target_rank(entry),
            "title_lines_as_published": title_lines,
            "standard_state": "p° = 0.1 MPa (as published by JANAF)",
            "units_as_published": {
                "temperature": "K",
                "heat_capacity": "J K^-1 mol^-1",
                "entropy": "J K^-1 mol^-1",
                "negative_gibbs_enthalpy_function": "J K^-1 mol^-1",
                "enthalpy_increment": "kJ mol^-1",
                "formation_enthalpy": "kJ mol^-1",
                "formation_gibbs_energy": "kJ mol^-1",
                "log10_formation_equilibrium_constant": "dimensionless",
            },
            "values": values,
            "parse_ambiguities": ambiguities,
        },
    }


def write_yaml(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=120)
    path.write_text(rendered, encoding="utf-8")


def main() -> int:
    args = cli()
    root = args.output_root.resolve()
    cache_root = root / "source-cache"
    index_cache = cache_root / "formula.html"
    index_payload = fetch_cached(INDEX_URL, index_cache, args.delay_seconds, args.refresh)
    entries = parse_index(index_payload)
    selected = entries[: args.limit] if args.limit is not None else entries
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source_id": "nist-janaf-4th",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "index_url": INDEX_URL,
        "index_cache_path": index_cache.as_posix(),
        "index_sha256": hashlib.sha256(index_payload).hexdigest(),
        "index_table_count": len(entries),
        "selected_table_count": len(selected),
        "target_formula_compositions": list(TARGET_FORMULAS),
        "entries": [],
    }
    if not args.index_only:
        for entry in selected:
            table_id = entry["table_id"]
            cache_path = cache_root / "tables" / f"{table_id}.txt"
            output_path = root / "tables" / f"{table_id}.yaml"
            record = dict(entry)
            record["target_rank"] = target_rank(entry)
            try:
                raw = fetch_cached(entry["download_url"], cache_path, args.delay_seconds, args.refresh)
                parsed = parse_table(raw, entry, cache_path)
                write_yaml(output_path, parsed)
                record.update(
                    {
                        "status": "harvested",
                        "row_count": len(parsed["table"]["values"]),
                        "ambiguity_count": len(parsed["table"]["parse_ambiguities"]),
                        "output_path": output_path.as_posix(),
                        "source_cache_path": cache_path.as_posix(),
                    }
                )
            except Exception as exc:
                record.update({"status": "failed", "error": str(exc)})
            manifest["entries"].append(record)
            if record["status"] == "harvested":
                print(f"{entry['formula']} {table_id} {record['row_count']} ok", flush=True)
            else:
                print(f"{entry['formula']} {table_id} FAILED {record['error']}", flush=True)
    else:
        manifest["entries"] = [{**entry, "target_rank": target_rank(entry), "status": "index_only"} for entry in selected]
    write_yaml(root / "manifest.yaml", manifest)
    failed = sum(entry["status"] == "failed" for entry in manifest["entries"])
    print(f"manifest={root / 'manifest.yaml'} index={len(entries)} selected={len(selected)} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
