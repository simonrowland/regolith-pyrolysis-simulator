#!/usr/bin/env python3
"""Harvest the public NIST-JANAF table corpus into traceable YAML files."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.reference_data.janaf import (  # noqa: E402
    COMPILATION_ROLE,
    COMPILATION_SOURCE,
    NON_STOICHIOMETRIC_TABLES,
    NON_STOICH_AMBIGUITY_REASON,
    SCHEMA_VERSION,
    formula_elements,
    formula_normalised,
    parse_janaf_txt,
)


BASE_URL = "https://janaf.nist.gov/"
INDEX_URL = urllib.parse.urljoin(BASE_URL, "formula.html")
USER_AGENT = (
    "RegolithPyrolysisSimulator-corpus-harvester/1.0 "
    "(public thermochemical reference-data research; polite cached client)"
)
TABLE_HREF_RE = re.compile(
    r"(?:^|/)(?:tables/)?([A-Za-z]{1,2}-\d+)\.html$",
    re.IGNORECASE,
)
FEEDSTOCK_ELEMENTS = (
    "Na", "K", "Fe", "Mg", "Si", "Ca", "Al", "Cr", "Mn", "Ti", "O", "P",
    "S", "Cl", "F", "H", "C", "Ni", "Zn", "Ba", "Sr", "Li", "Rb", "Cs",
    "Cu", "Co", "V", "Zr", "Nb", "Mo", "W", "Sn", "Pb", "Ga", "Ge", "B",
    "N", "Sc", "Y", "La", "Ce", "Nd", "Sm", "Eu", "Gd", "Dy", "Er", "Yb",
    "Lu", "Hf", "Th", "U", "Ta", "Ag", "Au", "Pt", "In", "Cd", "Bi", "Sb",
    "As", "Se", "Te", "Br", "I", "Ar", "He", "Ne",
)
TARGET_FORMULAS = (
    "Na", "K", "Fe", "Mg", "Si", "SiO", "SiO2", "Ca", "Al", "Cr", "Mn", "Ti",
    "O", "O2", "P", "S", "Cl", "F", "H2O", "CO", "CO2", "Ni", "Zn", "Na2O",
    "NaO", "K2O", "KO", "FeO", "Fe2O3", "MgO", "CaO", "Al2O3", "TiO2", "Cr2O3",
    "MnO", "P2O5", "PO", "PO2",
)
STAGE0_METALS = frozenset({"Na", "K", "Fe", "Mg", "Ca", "Al", "Cr", "Mn", "Ti", "Ni", "Zn"})
STAGE0_ANIONS = frozenset({"Cl", "F", "S"})
FOREIGN_ELEMENTS = frozenset({"Be", "D", "Hg", "Kr", "Xe", "Rn"})
NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?$")


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        match = TABLE_HREF_RE.search(href)
        if match:
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


def composition(formula_label: str) -> dict[str, float] | None:
    from simulator.reference_data.janaf import formula_composition

    parsed = formula_composition(formula_label)
    if parsed is None:
        return None
    return dict(parsed)


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


def formula_index_rows(payload: bytes) -> list[dict[str, Any]]:
    source = payload.decode("utf-8", errors="strict")
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
    return rows


def table_id_from_href(href: str) -> str:
    match = TABLE_HREF_RE.search(href)
    if not match:
        raise ValueError(f"unrecognised JANAF table href: {href!r}")
    return match.group(1)


def attach_table_identity(row: dict[str, Any], href: str) -> dict[str, Any]:
    url = urllib.parse.urljoin(urllib.parse.urljoin(BASE_URL, "tables/"), href)
    if "/tables/" not in urllib.parse.urlparse(url).path:
        url = urllib.parse.urljoin(BASE_URL, f"tables/{Path(href).name}")
    table_id = table_id_from_href(href)
    row = dict(row)
    row.update(
        {
            "table_id": table_id,
            "url": url if url.endswith(".html") else f"{url}.html",
            "download_url": urllib.parse.urljoin(BASE_URL, f"tables/{table_id}.txt"),
        }
    )
    if not row["url"].endswith(".html"):
        row["url"] = urllib.parse.urljoin(BASE_URL, f"tables/{table_id}.html")
    else:
        path = urllib.parse.urlparse(row["url"]).path
        if not path.endswith(f"/tables/{table_id}.html"):
            row["url"] = urllib.parse.urljoin(BASE_URL, f"tables/{table_id}.html")
    return row


def parse_index(payload: bytes) -> list[dict[str, Any]]:
    """Parse formula.html. Live pages have rows but no per-table links."""

    source = payload.decode("utf-8", errors="strict")
    link_parser = LinkParser()
    link_parser.feed(source)
    links = list(dict.fromkeys(link_parser.links))
    rows = formula_index_rows(payload)
    if links and len(rows) == len(links):
        entries = [attach_table_identity(row, href) for row, href in zip(rows, links, strict=True)]
        return sorted(entries, key=lambda item: (target_rank(item), item.get("jcode", 0), item["table_id"]))
    if links and len(rows) != len(links):
        raise ValueError(f"formula index rows/links mismatch: {len(rows)} rows, {len(links)} links")
    # Live 2026-09 site: formula.html is a <pre> listing with no table links.
    return rows


def parse_element_index(payload: bytes, element: str) -> list[dict[str, Any]]:
    """Parse tables/{El}-index.html. This is the live table-ID source."""

    source = payload.decode("utf-8", errors="strict")
    link_parser = LinkParser()
    link_parser.feed(source)
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for href in link_parser.links:
        table_id = table_id_from_href(href)
        if table_id in seen:
            continue
        seen.add(table_id)
        entries.append(
            attach_table_identity(
                {
                    "formula_label": element,
                    "name": "",
                    "index_element": element,
                },
                href,
            )
        )
    return entries


def collect_live_entries(
    cache_root: Path,
    delay_seconds: float,
    refresh: bool,
) -> tuple[list[dict[str, Any]], bytes, str]:
    """Enumerate in-scope tables the way the 2026-09-06 harvest did.

    formula.html supplies the official row census. Per-element index pages
    supply table IDs, because the live formula index has no hrefs.
    """

    index_cache = cache_root / "formula.html"
    index_payload = fetch_cached(INDEX_URL, index_cache, delay_seconds, refresh)
    formula_rows = parse_index(index_payload)
    by_id: dict[str, dict[str, Any]] = {}
    skipped_404: list[str] = []
    for element in FEEDSTOCK_ELEMENTS:
        href = f"tables/{element}-index.html"
        url = urllib.parse.urljoin(BASE_URL, href)
        cache_path = cache_root / "indexes" / f"{element}-index.html"
        try:
            payload = fetch_cached(url, cache_path, delay_seconds, refresh)
        except RuntimeError as exc:
            if "404" in str(exc) or "Not Found" in str(exc):
                skipped_404.append(element)
                continue
            raise
        if not payload.strip() or b"404" in payload[:80]:
            skipped_404.append(element)
            continue
        for entry in parse_element_index(payload, element):
            by_id.setdefault(entry["table_id"], entry)
    entries = sorted(by_id.values(), key=lambda item: (target_rank(item), item["table_id"]))
    _ = skipped_404
    _ = formula_rows
    return entries, index_payload, index_cache.as_posix()


def _title_formula_and_state(title_lines: list[str]) -> tuple[str, str, str]:
    """Return (name, formula_as_published, state) from the NIST .txt title line."""

    if not title_lines:
        return "", "", ""
    line = title_lines[0]
    left, sep, right = line.partition("\t")
    name = left.strip()
    state = ""
    published = ""
    paren = re.search(r"\(([^()]*)\)\s*$", name)
    if paren:
        published = paren.group(1).strip()
        name = name[: paren.start()].strip()
    if right:
        right = right.strip()
        state_match = re.search(r"\((ref|cr|l|cr,l|g|l,g|fl)\)$", right)
        if state_match:
            state = state_match.group(1)
            hill = right[: state_match.start()]
            if not published:
                published = hill
    return name, published, state


def parse_table(payload: bytes, entry: dict[str, Any], cache_path: Path) -> dict[str, Any]:
    """Parse a live NIST ``.txt`` (``T(K)`` header) into the compilation YAML schema."""

    table_id = str(entry["table_id"])
    parsed = parse_janaf_txt(
        payload,
        table_id=table_id,
        url=str(entry["url"]),
        download_url=str(entry["download_url"]),
    )
    name, title_formula, state = _title_formula_and_state(parsed.title_lines)
    if table_id in NON_STOICHIOMETRIC_TABLES:
        formula_as_published = NON_STOICHIOMETRIC_TABLES[table_id]["formula_as_published"]
    else:
        formula_as_published = str(
            entry.get("formula_label") or title_formula or entry.get("formula") or ""
        )
        if title_formula and (
            any(char in title_formula for char in ".+-")
            and not any(char in formula_as_published for char in ".+-")
        ):
            formula_as_published = title_formula
    if not formula_as_published:
        raise ValueError(f"{table_id}: missing printed formula")
    normalised = formula_normalised(formula_as_published)
    ambiguities = list(parsed.parse_ambiguities)
    if table_id in NON_STOICHIOMETRIC_TABLES:
        meta = NON_STOICHIOMETRIC_TABLES[table_id]
        if not any(item.get("kind") == "non_stoichiometric_formula_not_rewritten" for item in ambiguities):
            ambiguities.append(
                {
                    "kind": "non_stoichiometric_formula_not_rewritten",
                    "formula_as_published": formula_as_published,
                    "formula_normalised": normalised,
                    "previous_integerised_formula": meta["previous_integerised_formula"],
                    "reason": NON_STOICH_AMBIGUITY_REASON,
                }
            )
    title_as_published = parsed.title_lines[0] if parsed.title_lines else ""
    if " | " not in title_as_published and len(parsed.title_lines) == 1:
        # Preserve the two-field title as a single published string.
        title_as_published = parsed.title_lines[0].replace("\t", "  |  ")
    index_entry = {
        "formula": formula_as_published,
        "formula_as_published": formula_as_published,
        "formula_normalised": normalised,
        "name": name or entry.get("name") or "",
        "state": state or entry.get("state") or "not_parsed",
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "source_id": "nist-janaf-4th",
        "source": {
            "citation": COMPILATION_SOURCE["citation"],
            "database": COMPILATION_SOURCE["database"],
            "doi": COMPILATION_SOURCE["doi"],
            "url": BASE_URL,
            "last_data_update": 1998,
            "licence": COMPILATION_SOURCE["licence"],
        },
        "compilation_role": dict(COMPILATION_ROLE),
        "extraction": {
            "method": "machine parse of NIST tab-delimited table download; source bytes cached unchanged",
            "date": date.today().isoformat(),
            "user_agent": USER_AGENT,
            "source_cache_path": cache_path.as_posix(),
            "source_sha256": hashlib.sha256(payload).hexdigest(),
            "review_status": "machine_transcribed_unreviewed",
        },
        "table": {
            "table_id": table_id,
            "url": entry["url"],
            "download_url": entry["download_url"],
            "index_entry": index_entry,
            "title_as_published": title_as_published,
            "standard_state_as_published": "p° = 0.1 MPa (as published by JANAF)",
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
            "values": parsed.values,
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
    parsed_index = parse_index(index_payload)
    if parsed_index and parsed_index[0].get("table_id"):
        entries = parsed_index
    else:
        entries, index_payload, _index_cache_path = collect_live_entries(
            cache_root, args.delay_seconds, args.refresh
        )
    selected = entries[: args.limit] if args.limit is not None else entries
    run_ledger: dict[str, Any] = {
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
            if output_path.exists() and not args.refresh:
                record.update(
                    {
                        "status": "skipped_existing",
                        "output_path": output_path.as_posix(),
                    }
                )
                run_ledger["entries"].append(record)
                print(f"{entry.get('formula_label', '')} {table_id} skipped_existing", flush=True)
                continue
            try:
                raw = fetch_cached(entry["download_url"], cache_path, args.delay_seconds, args.refresh)
                parsed = parse_table(raw, entry, cache_path)
                published = parsed["table"]["index_entry"]["formula_as_published"]
                if set(formula_elements(published)) & FOREIGN_ELEMENTS:
                    record.update(
                        {
                            "status": "skipped_foreign_element",
                            "formula_as_published": published,
                        }
                    )
                    run_ledger["entries"].append(record)
                    print(f"{published} {table_id} skipped_foreign_element", flush=True)
                    continue
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
            run_ledger["entries"].append(record)
            formula = entry.get("formula_label") or entry.get("formula") or ""
            if record["status"] == "harvested":
                print(f"{formula} {table_id} {record['row_count']} ok", flush=True)
            else:
                print(f"{formula} {table_id} FAILED {record['error']}", flush=True)
    else:
        run_ledger["entries"] = [{**entry, "target_rank": target_rank(entry), "status": "index_only"} for entry in selected]
    # The compilation manifest is rebuilt by tools/build_janaf_compilation_manifest.py.
    # A harvest run ledger stays next to the source-cache (gitignored).
    write_yaml(cache_root / "harvest-run.yaml", run_ledger)
    failed = sum(entry.get("status") == "failed" for entry in run_ledger["entries"])
    print(
        f"harvest-run={cache_root / 'harvest-run.yaml'} index={len(entries)} "
        f"selected={len(selected)} failed={failed}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
