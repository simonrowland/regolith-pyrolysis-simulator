"""Transcribe Kelley & King (1961) Bulletin 592 from MinerU table HTML."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import yaml

from simulator.reference_data.kelley_king_1961_usbm_b592_loader import (
    _formula_integrity_issues,
    _table6_line_wrap_groups,
)
from tools.harvest_atct_compilation import feedstock_coverage


SOURCE_ID = "kelley-king-1961-usbm-b592"
ROOT = Path(__file__).resolve().parent
CORPUS_RAW = Path("/Users/simonrowland/Repos/regolith-corpus/raw") / SOURCE_ID
MINERU_ROOT = Path("/Users/simonrowland/Repos/regolith-corpus/text") / SOURCE_ID / "mineru"
PDF = CORPUS_RAW / f"{SOURCE_ID}.pdf"
ACCESS_DATE = "2026-09-08"
EXPECTED_BLOCKS = 25
EXPECTED_RECORDS = 1418
EXPECTED_BLOCKS_BY_TABLE = {1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 17, 7: 3}


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def parse_html_table(source: str) -> list[list[str]]:
    parser = TableParser()
    parser.feed(source)
    return parser.rows


def flatten_mineru(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(flatten_mineru(item) for item in value)
    if isinstance(value, dict):
        for key in ("content", "math_content", "paragraph_content", "title_content"):
            if key in value:
                return flatten_mineru(value[key])
    return ""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_yaml(path: Path, value: Any) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")


def table_number(caption: str) -> int | None:
    match = re.search(r"TABLE\s*(\d+)", caption, re.IGNORECASE)
    return int(match.group(1)) if match else None


def mineru_blocks() -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for path in sorted(MINERU_ROOT.glob("chunk-p*-p*/*content_list_v2.json")):
        match = re.search(r"chunk-p(\d+)-p", path.parent.name)
        if not match:
            continue
        first_page = int(match.group(1))
        pages = json.loads(path.read_text(encoding="utf-8"))
        for page_offset, items in enumerate(pages):
            pdf_page = first_page + page_offset
            for item_index, item in enumerate(items):
                if not isinstance(item, dict) or item.get("type") != "table":
                    continue
                content = item.get("content", {})
                caption = flatten_mineru(content.get("table_caption", [])).strip()
                number = table_number(caption)
                if number not in EXPECTED_BLOCKS_BY_TABLE:
                    continue
                html = content.get("html", "")
                rows = parse_html_table(html)
                if len(rows) < 2:
                    continue
                image_relative = content.get("image_source", {}).get("path")
                image_path = path.parent / image_relative
                blocks.append(
                    {
                        "block_index": len(blocks) + 1,
                        "table_number": number,
                        "pdf_page": pdf_page,
                        "printed_page": pdf_page - 4,
                        "item_index": item_index,
                        "caption_raw": caption,
                        "footnotes_raw": flatten_mineru(content.get("table_footnote", [])).strip(),
                        "html": html,
                        "image_path": str(image_path.relative_to(MINERU_ROOT)),
                        "image_sha256": sha256(image_path),
                    }
                )
    if len(blocks) != EXPECTED_BLOCKS:
        raise RuntimeError(f"expected {EXPECTED_BLOCKS} numeric table blocks, found {len(blocks)}")
    counts = {number: sum(block["table_number"] == number for block in blocks) for number in range(1, 8)}
    if counts != EXPECTED_BLOCKS_BY_TABLE:
        raise RuntimeError(f"physical table census differs: {counts}")
    return blocks


def build_numbered_table_census(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    markdown = MINERU_ROOT / "chunk-p001-p040" / f"{SOURCE_ID}-p001-p040.md"
    lines = markdown.read_text(encoding="utf-8").splitlines()
    start = lines.index("# TABLES")
    entries = []
    for source_line_index, line in enumerate(lines[start + 1 :], start=start + 2):
        match = re.match(r"^(\d+)\.\s+(.+?)\s+(\d+)\s*$", line.strip())
        if not match:
            if entries and len(entries) == 7:
                break
            continue
        number = int(match.group(1))
        if number != len(entries) + 1:
            continue
        matching = [block for block in blocks if block["table_number"] == number]
        entries.append(
            {
                "table_number": number,
                "title_as_published": match.group(2),
                "start_page_as_listed": int(match.group(3)),
                "printed_page_range": [min(item["printed_page"] for item in matching), max(item["printed_page"] for item in matching)],
                "pdf_page_range": [min(item["pdf_page"] for item in matching), max(item["pdf_page"] for item in matching)],
                "physical_block_count": len(matching),
                "census_source": {"mineru_markdown_line": source_line_index},
            }
        )
    if len(entries) != 7:
        raise RuntimeError(f"bulletin list of tables yielded {len(entries)} entries")
    return entries


def run_tesseract(block: dict[str, Any]) -> str:
    image = MINERU_ROOT / block["image_path"]
    result = subprocess.run(
        ["tesseract", str(image), "stdout", "--psm", "6"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return " ".join(result.stdout.split()) if result.returncode == 0 else ""


def add_raster_readings(blocks: list[dict[str, Any]], workers: int) -> None:
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(run_tesseract, block): index for index, block in enumerate(blocks)}
        for future in as_completed(futures):
            index = futures[future]
            blocks[index]["raster_ocr"] = future.result()
            print(f"TABLE {index + 1:02d}/{EXPECTED_BLOCKS}: raster OCR checked", flush=True)


RAW_NUMBER = re.compile(r"[+\-−]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)")
CONFUSABLE = re.compile(r"(?<![A-Za-z])[oOlISB](?=[.,\d])|(?<=\d)[oOlISB]", re.IGNORECASE)
MISSING = re.compile(r"^[\s.\-—–]*$")


def normalized_number(token: str) -> str:
    return token.replace(",", "").replace("−", "-").lstrip("+")


def numeric_surface(raw: str) -> str:
    """Exclude printed footnote ordinals while preserving the source string elsewhere."""
    return re.sub(r"^\s*\$?\s*\^\s*\{\d+\}\s*", "", raw)


def raster_alignment(raw_rows: list[list[str]], raster_ocr: str) -> set[tuple[int, int, int]]:
    source: list[str] = []
    coordinates: list[tuple[int, int, int]] = []
    for row_index, row in enumerate(raw_rows):
        for column_index, raw in enumerate(row):
            for token_index, token in enumerate(RAW_NUMBER.findall(numeric_surface(raw))):
                source.append(normalized_number(token))
                coordinates.append((row_index, column_index, token_index))
    raster = [normalized_number(item) for item in RAW_NUMBER.findall(raster_ocr)]
    matcher = difflib.SequenceMatcher(a=source, b=raster, autojunk=False)
    return {
        coordinates[index]
        for source_index, _raster_index, size in matcher.get_matching_blocks()
        for index in range(source_index, source_index + size)
    }


def parsed_values(raw: str) -> list[float]:
    surface = numeric_surface(raw)
    tokens = RAW_NUMBER.findall(surface)
    if not tokens:
        return []
    scientific = re.search(
        r"([+\-−]?(?:\d+(?:\.\d+)?|\.\d+))\s*(?:×|x|\\times)\s*10\s*(?:\^\s*)?(?:\{)?([+\-−]?\d+)",
        surface,
    )
    if scientific:
        base = float(normalized_number(scientific.group(1)))
        exponent = int(normalized_number(scientific.group(2)))
        return [base * (10.0**exponent)]
    return [float(normalized_number(token)) for token in tokens]


def parse_cell(raw: str, row_index: int, column_index: int, aligned: set[tuple[int, int, int]]) -> dict[str, Any]:
    values = parsed_values(raw)
    tokens = RAW_NUMBER.findall(numeric_surface(raw))
    agreement = bool(tokens) and all((row_index, column_index, index) in aligned for index in range(len(tokens)))
    confusable = bool(CONFUSABLE.search(raw))
    nonnumeric_nonmissing = bool(re.search(r"\d", raw)) and not values and not MISSING.fullmatch(raw)
    suspect = bool(raw.strip()) and (confusable or nonnumeric_nonmissing or (bool(tokens) and not agreement))
    return {
        "raw": raw,
        "value": values[0] if values else None,
        "parsed_values": values,
        "numeric_tokens": tokens,
        "footnote_markers": re.findall(r"\^\s*\{\d+\}|[†‡*]", raw),
        "ocr_suspect": suspect,
        "ocr_check": "raster_ocr_token_agreement" if agreement else "raster_ocr_token_disagreement",
    }


def plain_text(raw: str) -> str:
    value = raw.strip().strip("$")
    value = value.replace("\\cdot", "·")
    for command, symbol in (("\\alpha", "α"), ("\\beta", "β"), ("\\gamma", "γ")):
        value = value.replace(command, symbol)
    value = re.sub(r"\\(?:mathrm|text|operatorname)\s*\{([^{}]*)\}", r"\1", value)
    value = re.sub(r"[{}]", "", value)
    value = value.replace("\\dagger", "†").replace("\\ast", "*")
    value = re.sub(r"\\[A-Za-z]+", "", value)
    return " ".join(value.split())


def substance_metadata(raw: str) -> dict[str, Any]:
    published = raw.strip()
    markers = "".join(re.findall(r"[†‡*]+", plain_text(published)))
    cleaned = re.sub(r"(?:\\dagger|\\ast|\^|[†‡*])+\s*\$?$", "", published).strip()
    if cleaned.startswith("$") and not cleaned.endswith("$"):
        cleaned = cleaned[1:]
    plain = plain_text(cleaned)
    if ":" in plain:
        name, formula = (part.strip() for part in plain.split(":", 1))
    elif re.fullmatch(r"[A-Z][a-z]+", plain):
        name, formula = plain, None
    else:
        name, formula = plain, cleaned
    phase_match = re.search(r"\((?:c|l|g|aq|gl)(?:,[^)]*)?\)\s*$", plain, re.IGNORECASE)
    phase = phase_match.group(0) if phase_match else None
    return {
        "substance_as_published": published,
        "formula_as_published": formula,
        "formula": re.sub(r"\s+", "", formula) if formula else None,
        "name_as_published": name,
        "phase_as_published": phase,
        "state_as_published": phase,
        "footnote_markers": markers,
    }


def metadata_image_check(raw: str, raster_ocr: str) -> dict[str, Any]:
    normalized = lambda value: re.sub(r"[^a-z0-9]+", "", plain_text(value).lower())
    needle = normalized(raw)
    haystack = normalized(raster_ocr)
    verified = bool(needle) and needle in haystack
    return {
        "ocr_suspect": not verified,
        "ocr_check": "raster_ocr_text_agreement" if verified else "raster_ocr_text_disagreement",
    }


def reference_temperature(block: dict[str, Any], aligned: set[tuple[int, int, int]], rows: list[list[str]]) -> dict[str, Any]:
    raw = "298.15"
    raster_verified = raw in block["raster_ocr"]
    return {
        "raw": raw,
        "value": 298.15,
        "parsed_values": [298.15],
        "numeric_tokens": [raw],
        "ocr_suspect": not raster_verified,
        "ocr_check": "raster_ocr_caption_agreement" if raster_verified else "raster_ocr_caption_disagreement",
        "column": "temperature",
    }


TABLE_COLUMNS = {
    1: ["substance", "entropy_theory", "entropy_experimental"],
    2: ["state", "wave_number", "quantum_weight", "energy", "partition_term", "energy_partition_term"],
    3: ["quantum_weight", "wave_number", "energy", "partition_term", "energy_partition_term"],
    4: ["substance", "entropy_spectroscopic", "entropy_third_law"],
    5: ["substance", "entropy_spectroscopic", "entropy_molecular_constants", "entropy_third_law"],
    6: [
        "substance", "cp_10_k", "cp_25_k", "cp_50_k", "cp_100_k", "cp_150_k", "cp_200_k", "cp_298_15_k",
        "entropy_third_law", "entropy_spectrographic_or_molecular_constants", "entropy_other_sources", "entropy_recommended",
    ],
    7: ["substance", "temperature", "type_of_change", "heat_absorbed"],
}
TABLE_UNITS = {
    1: {column: None for column in TABLE_COLUMNS[1]},
    2: {column: None for column in TABLE_COLUMNS[2]},
    3: {column: None for column in TABLE_COLUMNS[3]},
    4: {column: None for column in TABLE_COLUMNS[4]},
    5: {column: None for column in TABLE_COLUMNS[5]},
    6: {
        "substance": None,
        **{column: "cal./deg.-mole" for column in TABLE_COLUMNS[6][1:]},
    },
    7: {
        "substance": None,
        "temperature": "°K.",
        "type_of_change": None,
        "heat_absorbed": "cal./mole",
    },
}
TABLE6_TEMPERATURES = [("10° K.", 10.0), ("25° K.", 25.0), ("50° K.", 50.0), ("100° K.", 100.0), ("150° K.", 150.0), ("200° K.", 200.0), ("298.15° K.", 298.15)]

TABLE6_IMAGE_VERIFIED_SPLITS = {
    (102, "$Ba(c)$"): [
        ("$Ba(c)$", {10: "16.0±0.5", 11: "16.0±0.5"}),
        ("$Ba(g)$", {7: "4.97", 9: "40.67±0.01", 11: "40.67±0.01"}),
    ],
    (106, "HfCl3(c)"): [
        ("HfC(c)", {10: "10.9±0.3", 11: "10.9±0.3"}),
        ("HfCl4(c)", {1: "(1.18)", 2: "(4.98)", 3: "11.03", 4: "18.91", 5: "23.59", 6: "26.24", 7: "28.80", 8: "45.6±0.6", 11: "45.6±0.6"}),
    ],
}
TABLE6_IMAGE_VERIFIED_TOKEN_CORRECTIONS = {
    (103, "$\\mathrm{B}_{2}\\mathrm{H}_{5}(g)\\dagger$"): {0: "$\\mathrm{B}_{2}\\mathrm{H}_{6}(g)\\dagger$"},
    (104, "COCl3(g)†"): {0: "COCl2(g)†"},
    (104, "C2N4(g)†"): {0: "C2N2(g)†"},
    (116, "$TiBr_3(c)$ *"): {4: "16.92"},
}
TABLE6_IMAGE_VERIFIED_METADATA_CORRECTIONS = {
    (101, 13): ('AlF3(c)', 'AlF₃(c)'),
    (102, 6): ('Sb4O6(g)', 'Sb₄O₆(g)'),
    (102, 10): ('Sb2S3(c)', 'Sb₂S₃(c)'),
    (102, 17): ('$As_4(g)$', 'As₄(g)'),
    (102, 19): ('AsCl3(l)', 'AsCl₃(l)'),
    (102, 20): ('$AsF_3(g)$', 'AsF₃(g)'),
    (102, 21): ('$AsF_3(l) \\dagger$', 'AsF₃(l)†'),
    (102, 22): ('AsH3(g)†*', 'AsH₃(g)†*'),
    (102, 27): ('As4O6(g)', 'As₄O₆(g)'),
    (102, 28): ('As2O5(c)', 'As₂O₅(c)'),
    (102, 30): ('H2AsO2(aq)', 'H₂AsO₂(aq)'),
    (102, 33): ('H2AsO4-(aq)', 'H₂AsO₄⁻(aq)'),
    (102, 34): ('HAsO4--(aq)', 'HAsO₄⁻⁻(aq)'),
    (102, 35): ('AsO4---(aq)', 'AsO₄⁻⁻⁻(aq)'),
    (102, 37): ('At2(c)', 'At₂(c)'),
    (102, 43): ('Ba(BrO3)2·H2O(c)', 'Ba(BrO₃)₂·H₂O(c)'),
    (102, 46): ('BaCl2·2H2O(c)', 'BaCl₂·2H₂O(c)'),
    (102, 78): ('BiCl3(c)', 'BiCl₃(c)'),
    (102, 83): ('BiI(g)', 'BiI(g)'),
    (103, 14): ('B5H9(l)†', 'B₅H₉(l)†'),
    (103, 15): ('B5H9(g)', 'B₅H₉(g)'),
    (103, 22): ('B3N3H6(g)', 'B₃N₃H₆(g)'),
    (103, 23): ('B2O3(c)', 'B₂O₃(c)'),
    (103, 24): ('B2O3(g)', 'B₂O₃(g)'),
    (103, 32): ('Br-(aq)', 'Br⁻(aq)'),
    (103, 37): ('BrF5(g)', 'BrF₅(g)'),
    (103, 60): ('CdSiO3(c)', 'CdSiO₃(c)'),
    (103, 62): ('$\\mathrm{CdSO}_{4}\\cdot\\mathrm{H}_{2}\\mathrm{O}(c)$', 'CdSO₄·H₂O(c)'),
    (103, 63): ('$\\mathrm{CdSO}_{4}\\cdot8/3\\mathrm{H}_{2}\\mathrm{O}(c)$', 'CdSO₄·8/3H₂O(c)'),
    (103, 77): ('Ca2B2O5(c)', 'Ca₂B₂O₅(c)'),
    (103, 78): ('Ca3B2O6(c)', 'Ca₃B₂O₆(c)'),
    (103, 79): ('CaBr2(c)', 'CaBr₂(c)'),
    (103, 81): ('CaC2(c)', 'CaC₂(c)'),
    (103, 82): ('CaCO3(calcite)', 'CaCO₃(calcite)'),
    (104, 12): ('CaC2O4·H2O(c)', 'CaC₂O₄·H₂O(c)'),
    (104, 16): ('Ca2P2O7(β)', 'Ca₂P₂O₇(β)'),
    (104, 17): ('Ca3P2O8(α)', 'Ca₃P₂O₈(α)'),
    (104, 18): ('Ca3P2O8(β)', 'Ca₃P₂O₈(β)'),
    (104, 20): ('Ca10(PO4)6F2(c)', 'Ca₁₀(PO₄)₆F₂(c)'),
    (104, 21): ('Ca10(PO4)6(OH)2(c)', 'Ca₁₀(PO₄)₆(OH)₂(c)'),
    (104, 24): ('Ca2SiO4(β)', 'Ca₂SiO₄(β)'),
    (104, 25): ('Ca2SiO4(γ)', 'Ca₂SiO₄(γ)'),
    (104, 26): ('Ca3SiO5(c)', 'Ca₃SiO₅(c)'),
    (104, 27): ('Ca3Si2O7(c)', 'Ca₃Si₂O₇(c)'),
    (104, 28): ('CaSO4(insol.)', 'CaSO₄(insol.)'),
    (104, 29): ('CaSO4(sol.α)', 'CaSO₄(sol.α)'),
    (104, 30): ('CaSO4(sol.β)', 'CaSO₄(sol.β)'),
    (104, 31): ('CaSO4·1/2H2O(α)', 'CaSO₄·1/2H₂O(α)'),
    (104, 32): ('CaSO4·1/2H2O(β)', 'CaSO₄·1/2H₂O(β)'),
    (104, 33): ('CaSO4·2H2O(selenite)', 'CaSO₄·2H₂O(selenite)'),
    (104, 35): ('CaSO3(c)', 'CaSO₃(c)'),
    (104, 37): ('Ca3Ti2O7(c)', 'Ca₃Ti₂O₇(c)'),
    (104, 66): ('COBr2(g)', 'COBr₂(g)'),
    (104, 74): ('CN-(aq)', 'CN⁻(aq)'),
    (104, 75): ('CNBr(g)', 'CNBr(g)'),
    (104, 81): ('HCO3-(aq)', 'HCO₃⁻(aq)'),
    (104, 82): ('CO3--(aq)', 'CO₃⁻⁻(aq)'),
    (104, 83): ('CNO-(aq)', 'CNO⁻(aq)'),
    (104, 84): ('C2O4--(aq)', 'C₂O₄⁻⁻(aq)'),
    (105, 8): ('Ce2S3(c)', 'Ce₂S₃(c)'),
    (105, 14): ('CsAl(SO4)2·12H2O(c)', 'CsAl(SO₄)₂·12H₂O(c)'),
    (105, 19): ('CsI(g)', 'CsI(g)'),
    (105, 39): ('Cr7C3(c)', 'Cr₇C₃(c)'),
    (105, 40): ('Cr23C6(c)', 'Cr₂₃C₆(c)'),
    (105, 46): ('CrO4--(aq)', 'CrO₄⁻⁻(aq)'),
    (105, 68): ('Cu0.75Fe2.25O4(c)', 'Cu₀.₇₅Fe₂.₂₅O₄(c)'),
    (105, 79): ('CuO·CuSO4(c)', 'CuO·CuSO₄(c)'),
    (105, 80): ('CuSO4·H2O(c)', 'CuSO₄·H₂O(c)'),
    (105, 81): ('CuSO4·3H2O(c)', 'CuSO₄·3H₂O(c)'),
    (105, 82): ('CuSO4·5H2O(c)', 'CuSO₄·5H₂O(c)'),
    (105, 83): ('Cu2S(c)', 'Cu₂S(c)'),
    (106, 4): ('Eu2(SO4)3·8H2O(c)', 'Eu₂(SO₄)₃·8H₂O(c)'),
    (106, 17): ('Gd2(SO4)3·8H2O(c)*', 'Gd₂(SO₄)₃·8H₂O(c)*'),
    (106, 21): ('GaBr3(c)', 'GaBr₃(c)'),
    (106, 23): ('Ga2C2(g)', 'Ga₂C₂(g)'),
    (106, 30): ('Ga2O3(c)', 'Ga₂O₃(c)'),
    (106, 35): ('GeBr4(g)', 'GeBr₄(g)'),
    (106, 37): ('GeCl4(g)', 'GeCl₄(g)'),
    (106, 39): ('GeF4(g)', 'GeF₄(g)'),
    (106, 60): ('HfBr4(c)', 'HfBr₄(c)'),
    (106, 61): ('HfBr4(g)', 'HfBr₄(g)'),
    (106, 63): ('HfCl4(g)', 'HfCl₄(g)'),
    (106, 64): ('HfF4(c)', 'HfF₄(c)'),
    (106, 65): ('HfF4(g)', 'HfF₄(g)'),
    (106, 66): ('HfI4(c)', 'HfI₄(c)'),
    (106, 67): ('HfI4(g)', 'HfI₄(g)'),
    (106, 84): ('H+(aq)', 'H⁺(aq)'),
    (106, 85): ('HN3(g)', 'HN₃(g)'),
    (107, 8): ('DClO(g)', 'DClO(g)'),
    (107, 16): ('HNCS(g)', 'HNCS(g)'),
    (107, 20): ('HF2-(aq)', 'HF₂⁻(aq)'),
    (107, 25): ('HNO3(g)', 'HNO₃(g)'),
    (107, 26): ('DNO3(g)', 'DNO₃(g)'),
    (107, 33): ('HN2O2-(aq)', 'HN₂O₂⁻(aq)'),
    (107, 38): ('T2O(g)', 'T₂O(g)'),
    (107, 52): ('H2PO4-(aq)', 'H₂PO₄⁻(aq)'),
    (107, 53): ('HPO4--(aq)', 'HPO₄⁻⁻(aq)'),
    (107, 66): ('HSO3-(aq)', 'HSO₃⁻(aq)'),
    (107, 67): ('H2S2O8(aq)', 'H₂S₂O₈(aq)'),
    (108, 4): ('I2(g)', 'I₂(g)'),
    (108, 11): ('$IF_5(g)$', 'IF₅(g)'),
    (108, 17): ('$IrF_6(g)$', 'IrF₆(g)'),
    (108, 22): ('Fe+++(aq)', 'Fe⁺⁺⁺(aq)'),
    (108, 27): ('Fe2Br6(g)', 'Fe₂Br₆(g)'),
    (108, 33): ('Fe2Cl6(g)', 'Fe₂Cl₆(g)'),
    (108, 34): ('FeCo2O4(c)', 'FeCo₂O₄(c)'),
    (108, 37): ('FeI2(c)', 'FeI₂(c)'),
    (108, 39): ('Fe0.947O(c)*', 'Fe₀.₉₄₇O(c)*'),
    (108, 44): ('FeSO4·7H2O(c)*', 'FeSO₄·7H₂O(c)*'),
    (108, 46): ('Fe0.877S(c)*', 'Fe₀.₈₇₇S(c)*'),
    (108, 49): ('Fe1.11Te(c)*', 'Fe₁.₁₁Te(c)*'),
    (108, 51): ('Fe2TiO4(c)*', 'Fe₂TiO₄(c)*'),
    (108, 52): ('Fe2TiO5(c)', 'Fe₂TiO₅(c)'),
    (108, 65): ('Pb++(aq)', 'Pb⁺⁺(aq)'),
    (108, 68): ('PbCO3(c)', 'PbCO₃(c)'),
    (108, 69): ('PbO·PbCO3(c)', 'PbO·PbCO₃(c)'),
    (108, 82): ('Pb2O3(c)', 'Pb₂O₃(c)'),
    (108, 83): ('Pb3O4(c)', 'Pb₃O₄(c)'),
    (108, 88): ('PbSiO3(amorph.)', 'PbSiO₃(amorph.)'),
    (108, 89): ('Pb2SiO4(c)', 'Pb₂SiO₄(c)'),
    (109, 4): ('PbSO4·PbO(c)', 'PbSO₄·PbO(c)'),
    (109, 5): ('PbSO4·2PbO(c)', 'PbSO₄·2PbO(c)'),
    (109, 6): ('PbSO4·3PbO(c)', 'PbSO₄·3PbO(c)'),
    (109, 24): ('LiFeO2(c)', 'LiFeO₂(c)'),
    (109, 31): ('LiOH·H2O(c)', 'LiOH·H₂O(c)'),
    (109, 62): ('MgCl2·H2O(c)', 'MgCl₂·H₂O(c)'),
    (109, 63): ('MgCl2·2H2O(c)', 'MgCl₂·2H₂O(c)'),
    (109, 64): ('MgCl2·4H2O(c)', 'MgCl₂·4H₂O(c)'),
    (109, 65): ('MgCl2·6H2O(c)*', 'MgCl₂·6H₂O(c)*'),
    (109, 73): ('$Mg(OH)_2(c)$', 'Mg(OH)₂(c)'),
    (109, 82): ('Mg2SiO4(c)', 'Mg₂SiO₄(c)'),
    (109, 84): ('MgSO4·H2O(c)', 'MgSO₄·H₂O(c)'),
    (109, 85): ('MgSO4·6H2O(c)', 'MgSO₄·6H₂O(c)'),
    (110, 11): ('MnCl2(c)', 'MnCl₂(c)'),
    (110, 13): ('MnF2(c)*', 'MnF₂(c)*'),
    (110, 16): ('MnI2(c)', 'MnI₂(c)'),
    (110, 20): ('MnO2(c)*', 'MnO₂(c)*'),
    (110, 21): ('Mn2O3(c)*', 'Mn₂O₃(c)*'),
    (110, 30): ('MnS2O6·2H2O(c)', 'MnS₂O₆·2H₂O(c)'),
    (110, 31): ('MnS2O6(c)', 'MnS₂O₆(c)'),
    (110, 32): ('MnS2O6·6H2O(c)', 'MnS₂O₆·6H₂O(c)'),
    (110, 34): ('Hg(l)†', 'Hg(l)†'),
    (110, 37): ('Hg2++(aq)', 'Hg₂⁺⁺(aq)'),
    (110, 40): ('HgBr2(g)', 'HgBr₂(g)'),
    (110, 44): ('HgCl2(g)', 'HgCl₂(g)'),
    (110, 49): ('HgF2(g)', 'HgF₂(g)'),
    (110, 60): ('Hg2SO4(c)', 'Hg₂SO₄(c)'),
    (110, 67): ('MoF6(l)', 'MoF₆(l)'),
    (110, 68): ('MoO2(c)', 'MoO₂(c)'),
    (110, 83): ('Ni(CO)4(l)†', 'Ni(CO)₄(l)†'),
    (110, 85): ('NiCl2(c)*', 'NiCl₂(c)*'),
    (110, 90): ('NiTe1.1(c)', 'NiTe₁.₁(c)'),
    (111, 3): ('NiTe1.6(c)', 'NiTe₁.₆(c)'),
    (111, 5): ('Ni0.4Zn0.6Fe2O4(c)', 'Ni₀.₄Zn₀.₆Fe₂O₄(c)'),
    (111, 6): ('Ni0.3Zn0.7Fe2O4(c)', 'Ni₀.₃Zn₀.₇Fe₂O₄(c)'),
    (111, 7): ('Ni0.2Zn0.8Fe2O4(c)', 'Ni₀.₂Zn₀.₈Fe₂O₄(c)'),
    (111, 8): ('Ni0.1Zn0.9Fe2O4(c)', 'Ni₀.₁Zn₀.₉Fe₂O₄(c)'),
    (111, 12): ('NbC(c)', 'NbC(c)'),
    (111, 13): ('NbF5(c)', 'NbF₅(c)'),
    (111, 15): ('NbO2(c)', 'NbO₂(c)'),
    (111, 16): ('Nb2O5(c)', 'Nb₂O₅(c)'),
    (111, 18): ('N2(g)†', 'N₂(g)†'),
    (111, 20): ('NF3(g)†', 'NF₃(g)†'),
    (111, 23): ('N2O(g)†', 'N₂O(g)†'),
    (111, 24): ('NO(g)†', 'NO(g)†'),
    (111, 27): ('N2O4(g)†', 'N₂O₄(g)†'),
    (111, 30): ('NO3-(aq)', 'NO₃⁻(aq)'),
    (111, 32): ('N2O2--(aq)', 'N₂O₂⁻⁻(aq)'),
    (111, 39): ('NH3(g)†', 'NH₃(g)†'),
    (111, 47): ('NH3B3H7(c)†', 'NH₃B₃H₇(c)†'),
    (111, 52): ('NH4Cl*', 'NH₄Cl*'),
    (111, 59): ('NH4OH(l)†', 'NH₄OH(l)†'),
    (111, 62): ('NH4NO3(c)†', 'NH₄NO₃(c)†'),
    (111, 63): ('(NH4)2O(l)†', '(NH₄)₂O(l)†'),
    (111, 73): ('O2(g)†', 'O₂(g)†'),
    (111, 87): ('P4(g)', 'P₄(g)'),
    (112, 3): ('PCl5(g)', 'PCl₅(g)'),
    (112, 4): ('PCl5(c)', 'PCl₅(c)'),
    (112, 13): ('PO4---(aq)', 'PO₄⁻⁻⁻(aq)'),
    (112, 32): ('PuO2++(aq)', 'PuO₂⁺⁺(aq)'),
    (112, 44): ('KAlSi3O8(c)', 'KAlSi₃O₈(c)'),
    (112, 63): ('K2Cr2O7(c)', 'K₂Cr₂O₇(c)'),
    (112, 64): ('K3Co(CN)6(c)', 'K₃Co(CN)₆(c)'),
    (112, 70): ('KHF2(c)', 'KHF₂(c)'),
    (112, 81): ('K2PtCl6(c)', 'K₂PtCl₆(c)'),
    (113, 13): ('ReF6(g)', 'ReF₆(g)'),
    (113, 14): ('ReO3(c)', 'ReO₃(c)'),
    (113, 17): ('ReO4-(aq)', 'ReO₄⁻(aq)'),
    (113, 55): ('Se6(g)', 'Se₆(g)'),
    (113, 56): ('Se--(aq)', 'Se⁻⁻(aq)'),
    (113, 61): ('SeO4--(aq)', 'SeO₄⁻⁻(aq)'),
    (113, 64): ('SeO3--(aq)', 'SeO₃⁻⁻(aq)'),
    (113, 70): ('SiBr4(g)', 'SiBr₄(g)'),
    (113, 72): ('SiC(hex.)', 'SiC(hex.)'),
    (113, 83): ('SiI4(g)', 'SiI₄(g)'),
    (113, 85): ('Si3N4(c)', 'Si₃N₄(c)'),
    (114, 13): ('Ag2CO3(c)', 'Ag₂CO₃(c)'),
    (114, 17): ('Ag2CrO4(c)', 'Ag₂CrO₄(c)'),
    (114, 22): ('Ag2H3IO6(c)*', 'Ag₂H₃IO₆(c)*'),
    (114, 28): ('Ag2O(c)*', 'Ag₂O(c)*'),
    (114, 30): ('Ag2SiO3(c)', 'Ag₂SiO₃(c)'),
    (114, 31): ('Ag2SO4(c)', 'Ag₂SO₄(c)'),
    (114, 32): ('Ag2S(α)', 'Ag₂S(α)'),
    (114, 33): ('Ag(NH3)2+(aq)', 'Ag(NH₃)₂⁺(aq)'),
    (114, 39): ('NaAlO2(c)', 'NaAlO₂(c)'),
    (114, 40): ('Na3AlF6(c)', 'Na₃AlF₆(c)'),
    (114, 43): ('NaAlSi3O8(c)', 'NaAlSi₃O₈(c)'),
    (114, 47): ('Na2B4O7(c)', 'Na₂B₄O₇(c)'),
    (114, 48): ('Na2B4O7(gl)', 'Na₂B₄O₇(gl)'),
    (114, 66): ('NaNH2(c)', 'NaNH₂(c)'),
    (114, 67): ('Na2O(c)', 'Na₂O(c)'),
    (114, 69): ('NaO2(c)*', 'NaO₂(c)*'),
    (114, 73): ('Na2SiO3(c)', 'Na₂SiO₃(c)'),
    (114, 74): ('Na2Si2O5(c)', 'Na₂Si₂O₅(c)'),
    (114, 75): ('Na4SiO4(c)', 'Na₄SiO₄(c)'),
    (114, 82): ('Na2Ti2O5(c)', 'Na₂Ti₂O₅(c)'),
    (114, 83): ('Na2Ti3O7(c)', 'Na₂Ti₃O₇(c)'),
    (114, 87): ('Sr++(aq)', 'Sr⁺⁺(aq)'),
    (115, 3): ('SrCl2(c)', 'SrCl₂(c)'),
    (115, 5): ('SrF2(c)', 'SrF₂(c)'),
    (115, 25): ('SCl2(g)', 'SCl₂(g)'),
    (115, 26): ('S2Cl2(g)', 'S₂Cl₂(g)'),
    (115, 29): ('SO2(g)†', 'SO₂(g)†'),
    (115, 38): ('SO2Cl2(g)', 'SO₂Cl₂(g)'),
    (115, 39): ('SO2Cl2(l)', 'SO₂Cl₂(l)'),
    (115, 42): ('SOCl2(g)', 'SOCl₂(g)'),
    (115, 43): ('SOF2(g)', 'SOF₂(g)'),
    (115, 48): ('Ta2O5(c)', 'Ta₂O₅(c)'),
    (115, 57): ('TeO2(c)', 'TeO₂(c)'),
    (116, 12): ('SnI4(g)', 'SnI₄(g)'),
    (116, 61): ('UF6(c)', 'UF₆(c)'),
    (116, 63): ('$UH_3(\\beta)$*', 'UH₃(β)*'),
    (116, 67): ('U4O9(c)', 'U₄O₉(c)'),
    (116, 69): ('UOCl2(c)', 'UOCl₂(c)'),
    (117, 5): ('VC(c)', 'VC(c)'),
    (117, 7): ('VCl3(c)*', 'VCl₃(c)*'),
    (117, 15): ('VOCl3(g)', 'VOCl₃(g)'),
    (117, 34): ('Zn3Sb2(c)', 'Zn₃Sb₂(c)'),
    (117, 35): ('Zn4Sb3(c)', 'Zn₄Sb₃(c)'),
    (117, 50): ('Zn2SiO4(c)', 'Zn₂SiO₄(c)'),
}


TABLE6_UNVERIFIED_IDENTITY_ROWS = frozenset(
    {
        (102, 41),
        (108, 2),
        (112, 28),
        (115, 24),
        (115, 32),
        (115, 33),
        (115, 34),
        (115, 35),
        (115, 36),
        (115, 37),
    }
)


TABLE6_IMAGE_VERIFIED_STRUCTURAL_METADATA = {
    (102, "Antimony-Con."): ("section_continuation_header", "Antimony—Con.", "Antimony—Con."),
    (103, "Boron-Con."): ("section_continuation_header", "Boron—Con.", "Boron—Con."),
    (104, "Calcium-Con."): ("section_continuation_header", "Calcium—Con.", "Calcium—Con."),
    (107, "Hydrogen-Con."): ("section_continuation_header", "Hydrogen—Con.", "Hydrogen—Con."),
    (109, "Lead-Con."): ("section_continuation_header", "Lead—Con.", "Lead—Con."),
    (111, "Nickel-Con."): ("section_continuation_header", "Nickel—Con.", "Nickel—Con."),
    (112, "Phosphorus-Con."): ("section_continuation_header", "Phosphorus—Con.", "Phosphorus—Con."),
    (112, "KMg2AlSi3-"): (
        "formula_continuation_prefix",
        "KMg3AlSi3-",
        "KMg3AlSi3- / O10F2(c) ... 75.9±0.5",
    ),
    (114, "Silicon-Con."): ("section_continuation_header", "Silicon—Con.", "Silicon—Con."),
    (114, "Na2SO4"): (
        "formula_continuation_prefix",
        "Na2SO4·",
        "Na2SO4· / 10H2O(c) ... 140.0±0.2",
    ),
    (115, "Strontium-Con."): ("section_continuation_header", "Strontium—Con.", "Strontium—Con."),
    (116, "Tin-Con."): ("section_continuation_header", "Tin—Con.", "Tin—Con."),
}
TABLE6_IMAGE_VERIFIED_METADATA_ANNOTATIONS = {
    (107, "HNO2(equ1,g)"): {
        "kind": "image_verified_printed_superscript_footnote_marker",
        "printed_token": "HNO2(equ¹,g)",
        "footnote_marker": "1",
        "quote": "HNO2(equ¹,g) ... 60.8±0.3; ¹equ = equilibrium.",
        "basis": "The 300-dpi PDF page render proves 1 is a printed superscript footnote marker, not an OCR error or phase qualifier.",
    }
}

TABLE6_IMAGE_VERIFIED_WRAPPED_SUBSTANCES = {
    (102, ("$Ba_{2.50}Sr_{10-47}$", "$TiO_3(c)$")): {
        "substance_offset": 0,
        "printed_fragments": ("Ba0.543Sr0.457", "TiO3(c)"),
        "formula": "Ba0.543Sr0.457TiO3(c)",
    },
    (109, ("$Li_0.05Zn_0.90$", "$Fe_2.05O_4(c)$", "(annealed)*")): {
        "substance_offset": 2,
        "printed_fragments": ("Li0.05Zn0.90", "Fe2.05O4(c)", "(annealed)*"),
        "formula": "Li0.05Zn0.90Fe2.05O4(c,annealed)*",
    },
    (109, ("$Li_0.05Zn_0.90$", "$Fe_2.05O_4(c)$", "(guenched)")): {
        "substance_offset": 2,
        "printed_fragments": ("Li0.05Zn0.90", "Fe2.05O4(c)", "(quenched)"),
        "formula": "Li0.05Zn0.90Fe2.05O4(c,quenched)",
    },
    (109, ("$Mg_3La_2(NO_2)_12$", "$2H_2O(c)*$")): {
        "substance_offset": 1,
        "printed_fragments": ("Mg3La2(NO3)12·", "24H2O(c)*"),
        "formula": "Mg3La2(NO3)12·24H2O(c)*",
    },
    (111, ("$NH_{4}Al(SO_{4})_{2}$", "$12H_{2}O(c) \\uparrow$")): {
        "substance_offset": 1,
        "printed_fragments": ("NH4Al(SO4)2·", "12H2O(c)†"),
        "formula": "NH4Al(SO4)2·12H2O(c)†",
    },
    (111, ("$(NH_{4})_{2}O-3Al_{2}O_{3}$", "$4SO_{3} \\cdot 6H_{2}O(c)$")): {
        "substance_offset": 1,
        "printed_fragments": ("(NH4)2O·3Al2O3·", "4SO3·6H2O(c)"),
        "formula": "(NH4)2O·3Al2O3·4SO3·6H2O(c)",
    },
    (111, ("$NH_{4}Cr(SO_{4})_{2}$", "$12H_{2}O(c) \\uparrow$")): {
        "substance_offset": 1,
        "printed_fragments": ("NH4Cr(SO4)2·", "12H2O(c)†"),
        "formula": "NH4Cr(SO4)2·12H2O(c)†",
    },
    (112, ("KAl(SO4)2", "12H3O(c)†")): {
        "substance_offset": 1,
        "printed_fragments": ("KAl(SO4)2·", "12H2O(c)†"),
        "formula": "KAl(SO4)2·12H2O(c)†",
    },
    (112, ("K2O-3Al2O3", "5SO3-9H3O(c)")): {
        "substance_offset": 1,
        "printed_fragments": ("K2O·3Al2O3·", "5SO3·9H2O(c)"),
        "formula": "K2O·3Al2O3·5SO3·9H2O(c)",
    },
    (112, ("K2O-3Al2O3", "4SO3-6H3O(c, natural)")): {
        "substance_offset": 1,
        "printed_fragments": ("K2O·3Al2O3·", "4SO3·6H2O(c,natural)"),
        "formula": "K2O·3Al2O3·4SO3·6H2O(c,natural)",
    },
    (112, ("K2O-3Al2O3", "4SO3-6H3O(c, synthetic)")): {
        "substance_offset": 1,
        "printed_fragments": ("K2O·3Al2O3·", "4SO3·6H2O(c,synthetic)"),
        "formula": "K2O·3Al2O3·4SO3·6H2O(c,synthetic)",
    },
    (112, ("KMg2AlSi3-", "O10F2(c)")): {
        "substance_offset": 1,
        "printed_fragments": ("KMg3AlSi3-", "O10F2(c)"),
        "formula": "KMg3AlSi3-O10F2(c)",
    },
    (114, ("NaAlSi2O6", "H2O(c)")): {
        "substance_offset": 1,
        "printed_fragments": ("NaAlSi2O6·", "H2O(c)"),
        "formula": "NaAlSi2O6·H2O(c)",
    },
    (114, ("Na2SO4", "10H2O(c)")): {
        "substance_offset": 1,
        "printed_fragments": ("Na2SO4·", "10H2O(c)"),
        "formula": "Na2SO4·10H2O(c)",
    },
    (116, ("$UO_2(NO_3)_2$", "$6H_2O(c)$")): {
        "substance_offset": 1,
        "printed_fragments": ("UO2(NO3)2·", "6H2O(c)"),
        "formula": "UO2(NO3)2·6H2O(c)",
    },
}


def native_row(
    raw_row: list[str],
    columns: list[str],
    row_index: int,
    aligned: set[tuple[int, int, int]],
    numeric_columns: set[str],
    *,
    alignment_column_offset: int = 0,
) -> dict[str, Any]:
    if len(raw_row) != len(columns):
        raise RuntimeError(f"row {row_index} has {len(raw_row)} cells, expected {len(columns)}")
    cells = {}
    for column_index, (column, raw) in enumerate(zip(columns, raw_row, strict=True)):
        if column in numeric_columns:
            cells[column] = parse_cell(raw, row_index, column_index + alignment_column_offset, aligned)
            cells[column]["column"] = column
        else:
            cells[column] = {"raw": raw}
    return {"source_row_index": row_index, "raw": raw_row, "cells": cells}


def cp_monotonicity(record: dict[str, Any]) -> list[dict[str, Any]]:
    row = record["rows"][0]
    values = [(temperature, row["cells"][column]["value"], column) for (raw, temperature), column in zip(TABLE6_TEMPERATURES, TABLE_COLUMNS[6][1:8], strict=True)]
    disagreements = []
    for left, right in zip(values, values[1:]):
        if left[1] is not None and right[1] is not None and right[1] + 0.02 < left[1]:
            disagreements.append({
                "kind": "within_record_cp_monotonicity_reversal",
                "temperature_pair_k": [left[0], right[0]],
                "value_pair": [left[1], right[1]],
                "columns": [left[2], right[2]],
                "note": "Detector only; phase changes and printed values retained unchanged.",
            })
    return disagreements


def recommendation_disagreement(record: dict[str, Any]) -> list[dict[str, Any]]:
    row = record["rows"][0]["cells"]
    recommended = row["entropy_recommended"]["value"]
    candidates = [row[key]["value"] for key in ("entropy_third_law", "entropy_spectrographic_or_molecular_constants", "entropy_other_sources")]
    if recommended is None or not any(value is not None for value in candidates):
        return []
    if any(math.isclose(recommended, value, abs_tol=0.011) for value in candidates if value is not None):
        return []
    return [{
        "kind": "recommended_entropy_not_equal_to_printed_source_column",
        "recommended": recommended,
        "source_values": candidates,
        "note": "Internal consistency detector only; no value corrected.",
    }]


def record_base(record_id: str, number: int, block: dict[str, Any], metadata: dict[str, Any], rows: list[dict[str, Any]], grid: list[dict[str, Any]], column_labels: list[str]) -> dict[str, Any]:
    metadata_check = metadata_image_check(metadata["substance_as_published"], block["raster_ocr"])
    return {
        "record_id": record_id,
        "table_number": number,
        **metadata,
        "metadata_ocr_suspect": metadata_check["ocr_suspect"],
        "metadata_ocr_check": metadata_check["ocr_check"],
        "page": block["printed_page"],
        "pdf_page": block["pdf_page"],
        "caption_raw": block["caption_raw"],
        "post_table_notes_verbatim": block["footnotes_raw"],
        "column_labels_as_published": column_labels,
        "units_as_published": TABLE_UNITS[number],
        "temperature_grid": grid,
        "rows": rows,
        "source_ref": {"path": "source/mineru-tables.jsonl", "line": block["block_index"]},
        "source_image": {"path": block["image_path"], "sha256": block["image_sha256"]},
        "ambiguities": [],
        "corrections": [],
    }


def build_records(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    ordinals = {number: 0 for number in range(1, 8)}
    current_table7: dict[str, Any] | None = None
    table6_heading: str | None = None
    pending_table6_shift: dict[str, Any] | None = None
    for block in blocks:
        number = block["table_number"]
        raw_rows = parse_html_table(block["html"])
        aligned = raster_alignment(raw_rows, block["raster_ocr"])
        columns = TABLE_COLUMNS[number]
        if number in {1, 4, 5}:
            for row_index, raw_row in enumerate(raw_rows[1:], start=1):
                ordinals[number] += 1
                row = native_row(raw_row, columns, row_index, aligned, set(columns[1:]))
                metadata = substance_metadata(raw_row[0])
                record = record_base(f"table-{number:03d}-{ordinals[number]:04d}", number, block, metadata, [row], [reference_temperature(block, aligned, raw_rows)], raw_rows[0])
                records.append(record)
        elif number in {2, 3}:
            ordinals[number] += 1
            caption_match = re.search(r"for\s+(.+?)\s+at\s+298", block["caption_raw"], re.IGNORECASE)
            substance = caption_match.group(1).strip() if caption_match else block["caption_raw"]
            metadata = substance_metadata(substance)
            numeric_columns = set(columns[1:] if number == 2 else columns)
            rows = [native_row(raw_row, columns, row_index, aligned, numeric_columns) for row_index, raw_row in enumerate(raw_rows[1:], start=1)]
            records.append(record_base(f"table-{number:03d}-0001", number, block, metadata, rows, [reference_temperature(block, aligned, raw_rows)], raw_rows[0]))
        elif number == 6:
            grid = []
            for column_index, ((expected_raw, expected_value), raw) in enumerate(
                zip(TABLE6_TEMPERATURES, raw_rows[1][:7], strict=True)
            ):
                cell = parse_cell(raw, 1, column_index, aligned)
                if cell["value"] != expected_value:
                    raise RuntimeError(
                        f"unexpected Table 6 grid token on PDF page {block['pdf_page']}: {raw!r}"
                    )
                cell["column"] = "temperature"
                cell["expected_printed_form"] = expected_raw
                grid.append(cell)
            detected_wraps = _table6_line_wrap_groups(raw_rows)
            detected_tokens = {
                tuple(raw_rows[index][0] for index in group): group
                for group in detected_wraps
            }
            expected_tokens = {
                tokens
                for page, tokens in TABLE6_IMAGE_VERIFIED_WRAPPED_SUBSTANCES
                if page == block["printed_page"]
            }
            if set(detected_tokens) != expected_tokens:
                raise RuntimeError(
                    f"unreviewed Table 6 line-wrap candidates on printed page {block['printed_page']}: "
                    f"detected={sorted(detected_tokens)} expected={sorted(expected_tokens)}"
                )
            wrap_parts = {}
            for tokens, group in detected_tokens.items():
                spec = TABLE6_IMAGE_VERIFIED_WRAPPED_SUBSTANCES[
                    (block["printed_page"], tokens)
                ]
                for offset, row_index in enumerate(group):
                    wrap_parts[row_index] = (spec, offset)
            for row_index, raw_row in enumerate(raw_rows[2:], start=2):
                if raw_row[0].strip().endswith(":"):
                    table6_heading = raw_row[0].strip()
                    nonempty = {index: raw for index, raw in enumerate(raw_row[1:], start=1) if raw.strip()}
                    if nonempty:
                        if table6_heading != "Actinum:" or nonempty != {10: "15.0±1.0", 11: "15.0±1.0"}:
                            raise RuntimeError(f"unreviewed data attached to Table 6 group heading: {raw_row}")
                        table6_heading = "Actinium:"
                        pending_table6_shift = {"source_row_index": row_index, "cells": nonempty}
                    continue
                corrections = []
                wrap_part = wrap_parts.get(row_index)
                wrap_record_kind = None
                if wrap_part:
                    spec, offset = wrap_part
                    ocr_token = raw_row[0]
                    substance_offset = spec["substance_offset"]
                    printed_token = (
                        spec["formula"]
                        if offset == substance_offset
                        else spec["printed_fragments"][offset]
                    )
                    wrap_record_kind = (
                        None
                        if offset == substance_offset
                        else (
                            "formula_continuation_prefix"
                            if offset < substance_offset
                            else "formula_continuation_suffix"
                        )
                    )
                    corrected = list(raw_row)
                    corrected[0] = printed_token
                    corrections.append({
                        "kind": (
                            "image_verified_wrapped_substance_reconstruction"
                            if wrap_record_kind is None
                            else f"image_verified_{wrap_record_kind}_reclassification"
                        ),
                        "pdf_page": block["pdf_page"],
                        "page": block["printed_page"],
                        "source_row_index": row_index,
                        "column": "substance",
                        "ocr_token": ocr_token,
                        "printed_token": printed_token,
                        "quote": spec["formula"],
                        "basis": "The 300-dpi PDF page render proves these adjacent source rows are one wrapped substance formula.",
                    })
                    raw_row = corrected
                if pending_table6_shift is not None:
                    corrected = list(raw_row)
                    for column_index, raw in pending_table6_shift["cells"].items():
                        if corrected[column_index].strip():
                            raise RuntimeError("Actinium image correction target is not blank")
                        corrected[column_index] = raw
                        corrections.append(
                            {
                                "kind": "image_verified_row_alignment_correction",
                                "pdf_page": block["pdf_page"],
                                "page": block["printed_page"],
                                "source_row_index": row_index,
                                "source_heading_row_index": pending_table6_shift["source_row_index"],
                                "column": columns[column_index],
                                "ocr_token": raw,
                                "printed_token": raw,
                                "quote": f"Actinium: / Ac(c) ... {raw}",
                                "basis": "MinerU table crop inspected at original resolution; value is printed on the Ac(c) row.",
                            }
                        )
                    raw_row = corrected
                    pending_table6_shift = None
                token_corrections = TABLE6_IMAGE_VERIFIED_TOKEN_CORRECTIONS.get((block["printed_page"], raw_row[0]), {})
                if token_corrections:
                    corrected = list(raw_row)
                    for column_index, printed_token in token_corrections.items():
                        ocr_token = corrected[column_index]
                        corrected[column_index] = printed_token
                        corrections.append({
                            "kind": "image_verified_token_correction",
                            "pdf_page": block["pdf_page"],
                            "page": block["printed_page"],
                            "source_row_index": row_index,
                            "column": columns[column_index],
                            "ocr_token": ocr_token,
                            "printed_token": printed_token,
                            "quote": f"{corrected[0]} ... {printed_token}",
                            "basis": "Original-resolution page image proves the printed token.",
                        })
                    raw_row = corrected
                structural_metadata = TABLE6_IMAGE_VERIFIED_STRUCTURAL_METADATA.get(
                    (block["printed_page"], raw_row[0])
                )
                if wrap_record_kind:
                    structural_metadata = (
                        wrap_record_kind,
                        raw_row[0],
                        next(
                            correction["quote"]
                            for correction in corrections
                            if correction["kind"].endswith("_reclassification")
                        ),
                    )
                metadata_annotation = TABLE6_IMAGE_VERIFIED_METADATA_ANNOTATIONS.get(
                    (block["printed_page"], raw_row[0])
                )
                if structural_metadata and not wrap_record_kind:
                    record_kind, printed_token, quote = structural_metadata
                    ocr_token = raw_row[0]
                    corrected = list(raw_row)
                    corrected[0] = printed_token
                    corrections.append({
                        "kind": f"image_verified_{record_kind}_reclassification",
                        "pdf_page": block["pdf_page"],
                        "page": block["printed_page"],
                        "source_row_index": row_index,
                        "column": "substance",
                        "ocr_token": ocr_token,
                        "printed_token": printed_token,
                        "quote": quote,
                        "basis": "The 300-dpi PDF page render proves this source row is structural metadata, not an independent substance formula.",
                    })
                    raw_row = corrected
                metadata_correction = TABLE6_IMAGE_VERIFIED_METADATA_CORRECTIONS.get(
                    (block["printed_page"], row_index)
                )
                if metadata_correction:
                    ocr_token = raw_row[0]
                    printed_token, quote = metadata_correction
                    corrected = list(raw_row)
                    corrected[0] = printed_token
                    corrections.append({
                        "kind": "image_verified_metadata_token_correction",
                        "pdf_page": block["pdf_page"],
                        "page": block["printed_page"],
                        "source_row_index": row_index,
                        "column": "substance",
                        "ocr_token": ocr_token,
                        "printed_token": printed_token,
                        "quote": quote,
                        "basis": "The 300-dpi PDF page render proves the printed substance token.",
                    })
                    raw_row = corrected
                variants = [(raw_row, corrections)]
                split = TABLE6_IMAGE_VERIFIED_SPLITS.get((block["printed_page"], raw_row[0]))
                if split:
                    variants = []
                    for substance, printed_cells in split:
                        split_row = [""] * len(columns)
                        split_row[0] = substance
                        split_corrections = [{
                            "kind": "image_verified_merged_row_split",
                            "pdf_page": block["pdf_page"],
                            "page": block["printed_page"],
                            "source_row_index": row_index,
                            "column": "substance",
                            "ocr_token": raw_row[0],
                            "printed_token": substance,
                            "quote": substance,
                            "basis": "Original-resolution page image proves two printed substance rows were merged into one MinerU row.",
                        }]
                        for column_index, printed_token in printed_cells.items():
                            split_row[column_index] = printed_token
                            split_corrections.append({
                                "kind": "image_verified_merged_row_split",
                                "pdf_page": block["pdf_page"],
                                "page": block["printed_page"],
                                "source_row_index": row_index,
                                "column": columns[column_index],
                                "ocr_token": raw_row[column_index] or None,
                                "printed_token": printed_token,
                                "quote": f"{substance} ... {printed_token}",
                                "basis": "Original-resolution page image proves two printed substance rows were merged into one MinerU row.",
                            })
                        variants.append((split_row, split_corrections))
                for variant, variant_corrections in variants:
                    ordinals[number] += 1
                    row = native_row(variant, columns, row_index, aligned, set(columns[1:]))
                    for correction in variant_corrections:
                        cell = row["cells"][correction["column"]]
                        cell["ocr_suspect"] = True
                        cell["ocr_check"] = correction["kind"]
                    metadata = substance_metadata(variant[0])
                    if structural_metadata:
                        metadata.update(
                            {
                                "formula_as_published": None,
                                "formula": None,
                                "name_as_published": structural_metadata[1],
                                "phase_as_published": None,
                                "state_as_published": None,
                            }
                        )
                    record_id = f"table-006-{ordinals[number]:04d}"
                    record = record_base(record_id, number, block, metadata, [row], grid, raw_rows[0] + raw_rows[1])
                    record["heading_context_as_published"] = table6_heading
                    if structural_metadata:
                        record["record_kind"] = structural_metadata[0]
                    elif (block["printed_page"], row_index) in TABLE6_UNVERIFIED_IDENTITY_ROWS:
                        record["record_kind"] = "unverified_identity"
                    for correction in variant_corrections:
                        if correction["kind"] in {
                            "image_verified_metadata_token_correction",
                            "image_verified_wrapped_substance_reconstruction",
                        } or correction["kind"].endswith("_reclassification"):
                            correction["record_id"] = record_id
                            record["metadata_ocr_token"] = correction["ocr_token"]
                            record["metadata_ocr_suspect"] = True
                            record["metadata_ocr_check"] = correction["kind"]
                    if metadata_annotation:
                        record["footnote_markers"] = metadata_annotation["footnote_marker"]
                        record["metadata_ocr_suspect"] = False
                        record["metadata_ocr_check"] = metadata_annotation["kind"]
                        record["metadata_annotations"] = [
                            {
                                "record_id": record_id,
                                "pdf_page": block["pdf_page"],
                                "page": block["printed_page"],
                                "source_row_index": row_index,
                                "column": "substance",
                                **metadata_annotation,
                            }
                        ]
                    record["corrections"].extend(variant_corrections)
                    record["ambiguities"].extend(cp_monotonicity(record))
                    record["ambiguities"].extend(recommendation_disagreement(record))
                    records.append(record)
        else:
            for row_index, raw_row in enumerate(raw_rows[1:], start=1):
                if len(raw_row) == 4:
                    ordinals[number] += 1
                    metadata = substance_metadata(raw_row[0])
                    row = native_row(raw_row, columns, row_index, aligned, {"temperature", "heat_absorbed"})
                    current_table7 = record_base(f"table-007-{ordinals[number]:04d}", number, block, metadata, [row], [row["cells"]["temperature"]], raw_rows[0])
                    records.append(current_table7)
                elif len(raw_row) == 3 and current_table7 is not None:
                    expanded = [current_table7["substance_as_published"], *raw_row]
                    row = native_row(
                        expanded,
                        columns,
                        row_index,
                        aligned,
                        {"temperature", "heat_absorbed"},
                        alignment_column_offset=-1,
                    )
                    current_table7["rows"].append(row)
                    current_table7["temperature_grid"].append(row["cells"]["temperature"])
                else:
                    raise RuntimeError(f"unrecoverable Table 7 row shape at PDF page {block['pdf_page']}: {raw_row}")
        print(f"TABLE {block['block_index']:02d}/{EXPECTED_BLOCKS}: transcribed ({len(records)} records)", flush=True)
    if len(records) != EXPECTED_RECORDS:
        raise RuntimeError(f"expected {EXPECTED_RECORDS} source records, found {len(records)}")
    unresolved_formula_integrity = [
        (record["record_id"], field, token, reason)
        for record in records
        for field, token, reason in _formula_integrity_issues(record)
    ]
    if unresolved_formula_integrity:
        raise RuntimeError(
            f"unreviewed formula-integrity candidates: {unresolved_formula_integrity}"
        )
    return records


def all_cells(value: Any):
    if isinstance(value, dict):
        if "raw" in value and "value" in value:
            yield value
        for child in value.values():
            yield from all_cells(child)
    elif isinstance(value, list):
        for child in value:
            yield from all_cells(child)


def add_magnitude_detectors(records: list[dict[str, Any]]) -> None:
    by_column: dict[tuple[int, str], list[float]] = {}
    for record in records:
        for row in record["rows"]:
            for column, cell in row["cells"].items():
                if "value" in cell and isinstance(cell["value"], (int, float)) and cell["value"] != 0:
                    by_column.setdefault((record["table_number"], column), []).append(abs(cell["value"]))
    medians = {}
    for key, values in by_column.items():
        ordered = sorted(values)
        medians[key] = ordered[len(ordered) // 2]
    for record in records:
        for row in record["rows"]:
            for column, cell in row["cells"].items():
                value = cell.get("value")
                median = medians.get((record["table_number"], column))
                if not isinstance(value, (int, float)) or value == 0 or not median:
                    continue
                ratio = abs(value) / median
                if 300 <= ratio <= 3000 or 300 <= 1 / ratio <= 3000:
                    record["ambiguities"].append({
                        "kind": "column_magnitude_factor_approximately_1000",
                        "column": column,
                        "raw": cell["raw"],
                        "value": value,
                        "column_median": median,
                        "ratio": ratio,
                        "image_cross_check": cell["ocr_check"],
                        "note": "Detector only; printed token retained unchanged.",
                    })


def write_access_status(sidecar: dict[str, Any]) -> None:
    path = ROOT.parent / "access-status.yaml"
    status = yaml.safe_load(path.read_text(encoding="utf-8"))
    status["updated"] = ACCESS_DATE
    status["sources"]["kelley_king_1961_usbm_b592"] = {
        "citation": sidecar["citation"],
        "report_number": "U.S. Bureau of Mines Bulletin 592",
        "access": "public_domain_us_government_work",
        "official_url": sidecar["located_by"]["official_url"],
        "retrieved_url": sidecar["retrieved_url"],
        "licence_basis": "United States Bureau of Mines government work; public domain in the United States.",
        "access_date": ACCESS_DATE,
        "local_status": "complete_ingest",
        "harvested_path": f"data/literature/compilations/{SOURCE_ID}/",
        "source_pdf_sha256": sidecar["sha256"],
        "source_pdf_bytes": sidecar["size"],
        "harvested_record_count": EXPECTED_RECORDS,
        "note": "Assessed entropy and heat-capacity tables; reference input only, never measured or battery-scored.",
    }
    write_yaml(path, status)


def build(workers: int) -> None:
    sidecar = yaml.safe_load((CORPUS_RAW / "sidecar.yaml").read_text(encoding="utf-8"))
    if sha256(PDF) != sidecar["sha256"]:
        raise RuntimeError("PDF SHA-256 differs from acquisition sidecar")
    blocks = mineru_blocks()
    census_entries = build_numbered_table_census(blocks)
    source_dir = ROOT / "source"
    records_dir = ROOT / "records"
    source_dir.mkdir(parents=True, exist_ok=True)
    records_dir.mkdir(parents=True, exist_ok=True)
    source_path = source_dir / "mineru-tables.jsonl"
    if source_path.exists():
        cached = [json.loads(line) for line in source_path.read_text(encoding="utf-8").splitlines()]
        native = [{key: value for key, value in block.items() if key != "raster_ocr"} for block in cached]
        if len(cached) == EXPECTED_BLOCKS and all("raster_ocr" in block for block in cached) and native == blocks:
            blocks = cached
        else:
            add_raster_readings(blocks, workers)
    else:
        add_raster_readings(blocks, workers)
    write_yaml(source_dir / "sidecar.yaml", sidecar)
    source_path.write_text("".join(json.dumps(block, ensure_ascii=False, separators=(",", ":")) + "\n" for block in blocks), encoding="utf-8")
    records = build_records(blocks)
    add_magnitude_detectors(records)
    for record in records:
        record["transcription_status"] = (
            "transcribed_with_ocr_ambiguities"
            if record["metadata_ocr_suspect"]
            or record["ambiguities"]
            or record["corrections"]
            or any(cell["ocr_suspect"] for cell in all_cells(record["rows"]))
            else "transcribed"
        )
    for old in records_dir.glob("*.json"):
        old.unlink()
    row_suspect_count = sum(cell["ocr_suspect"] for record in records for cell in all_cells(record["rows"]))
    grid_suspect_count = sum(cell["ocr_suspect"] for record in records for cell in record["temperature_grid"])
    metadata_suspect_count = sum(record["metadata_ocr_suspect"] for record in records)
    summary = {
        "numbered_table_count": 7,
        "physical_table_block_count": len(blocks),
        "record_count": len(records),
        "substance_count": sum(
            record.get("record_kind", "substance") == "substance"
            for record in records
        ),
        "transcribed_record_count": len(records),
        "untranscribed_record_count": 0,
        "numeric_cell_count": sum(1 for record in records for cell in all_cells(record["rows"])),
        "parsed_value_count": sum(cell["value"] is not None for record in records for cell in all_cells(record["rows"])),
        "temperature_grid_token_count": sum(len(record["temperature_grid"]) for record in records),
        "row_numeric_ocr_suspect_count": row_suspect_count,
        "temperature_grid_ocr_suspect_count": grid_suspect_count,
        "metadata_ocr_suspect_count": metadata_suspect_count,
        "ocr_suspect_count": row_suspect_count + grid_suspect_count + metadata_suspect_count,
        "identity_check_disagreement_count": sum(
            ambiguity["kind"] == "recommended_entropy_not_equal_to_printed_source_column"
            for record in records
            for ambiguity in record["ambiguities"]
        ),
        "correction_count": sum(len(record["corrections"]) for record in records),
    }
    source = {
        "database": "U.S. Bureau of Mines Bulletin 592",
        "authors": "Kelley, K. K. and King, E. G.",
        "version": "1961",
        "date_as_published": "1961",
        "official_url": sidecar["located_by"]["official_url"],
        "retrieved_url": sidecar["retrieved_url"],
        "licence": sidecar["licence"],
        "access_date": ACCESS_DATE,
    }
    entries = []
    suspect_examples = []
    for record in records:
        path = records_dir / f"{record['record_id']}.json"
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        suspects = [cell for cell in all_cells(record["rows"]) if cell["ocr_suspect"]]
        if (suspects or record["metadata_ocr_suspect"]) and len(suspect_examples) < 20:
            suspect_examples.append({"record_id": record["record_id"], "metadata": record["metadata_ocr_suspect"], "tokens": [cell["raw"] for cell in suspects[:5]]})
        entries.append({
            "record_id": record["record_id"],
            "formula": record["formula"],
            "formula_as_published": record["formula_as_published"],
            "phase": record["phase_as_published"],
            "name_as_published": record["name_as_published"],
            "metadata_ocr_suspect": record["metadata_ocr_suspect"],
            **({"record_kind": record["record_kind"]} if record.get("record_kind") else {}),
            **(
                {"metadata_annotations": record["metadata_annotations"]}
                if record.get("metadata_annotations")
                else {}
            ),
            **(
                {"metadata_ocr_token": record["metadata_ocr_token"]}
                if record.get("metadata_ocr_token")
                else {}
            ),
            "source": source,
            "original_record_text_location": record["source_ref"],
            "source_locator": {"pdf_page": record["pdf_page"], "printed_page": record["page"], "table_number": record["table_number"]},
            "path": f"records/{record['record_id']}.json",
            "row_count": len(record["rows"]),
            "ocr_suspect_count": len(suspects) + sum(cell["ocr_suspect"] for cell in record["temperature_grid"]) + int(record["metadata_ocr_suspect"]),
            "ambiguity_count": len(record["ambiguities"]),
            "ambiguities": record["ambiguities"],
            "sha256": sidecar["sha256"],
        })
    record_counts = {str(number): sum(record["table_number"] == number for record in records) for number in range(1, 8)}
    census = {
        "basis": "Bulletin's own TABLES list (PDF page 7) and all 25 physical table blocks",
        "numbered_table_count": 7,
        "physical_table_block_count": len(blocks),
        "record_count": len(records),
        "substance_count": sum(
            record.get("record_kind", "substance") == "substance"
            for record in records
        ),
        "record_ids": [record["record_id"] for record in records],
        "record_counts_by_table": record_counts,
        "tables": [{**entry, "record_count": record_counts[str(entry["table_number"])]} for entry in census_entries],
    }
    manifest = {
        "schema_version": "literature_compilation_manifest.v1",
        "source_id": SOURCE_ID,
        "source": source,
        "compilation_role": {"engine_reference_input": True, "validation_measurement": False, "scoring_eligible": False, "battery_refusal": "assessed_compilation_not_runtime_observable"},
        "source_files": [
            {"path": f"raw/{SOURCE_ID}/{SOURCE_ID}.pdf", "sha256": sidecar["sha256"], "storage": "external read-only corpus"},
            {"path": "source/mineru-tables.jsonl", "sha256": sha256(source_path)},
            {"path": "source/sidecar.yaml", "sha256": sha256(source_dir / "sidecar.yaml")},
            {"path": "source/image-verified-fixture.json", "sha256": sha256(source_dir / "image-verified-fixture.json")},
            {"path": "source/formula-audit.jsonl", "sha256": sha256(source_dir / "formula-audit.jsonl")},
        ],
        "census": census,
        "summary": summary,
        "ocr_policy": {
            "primary": "MinerU table HTML from five page-aligned chunks",
            "second_reading": "Tesseract on every MinerU table crop; exact token sequence agreement required for a clean numeric cell",
            "metadata": "Formula/name/phase clean only when normalized text is found in table-crop raster OCR",
            "numeric_repairs": False,
            "identity_checks": "Cp monotonicity, recommended-entropy source agreement, and factor-1000 column magnitude detectors only; never correct values",
        },
        "ocr_suspect_examples": suspect_examples,
        "corrections": [
            {"record_id": record["record_id"], **correction}
            for record in records
            for correction in record["corrections"]
        ],
        "untranscribed": [],
        "feedstock_element_coverage": feedstock_coverage([{"formula": record["formula"] or ""} for record in records]),
        "entries": entries,
    }
    write_yaml(ROOT / "manifest.yaml", manifest)
    (ROOT / "census.json").write_text(json.dumps(census, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_access_status(sidecar)
    print(json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ocr-workers", type=int, default=8)
    build(parser.parse_args().ocr_workers)
