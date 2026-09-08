"""Build the complete native-table ingest of USBM Bulletin 677."""

from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import struct
import subprocess
import sys
from collections import Counter
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.harvest_atct_compilation import feedstock_coverage


SOURCE_ID = "pankratz-1984-usbm-b677"
ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "data/literature/compilations" / SOURCE_ID
CORPUS = Path("/Users/simonrowland/Repos/regolith-corpus")
MINERU = CORPUS / "text" / SOURCE_ID / "mineru"
PDF = CORPUS / "raw" / SOURCE_ID / f"{SOURCE_ID}.pdf"
SIDECAR = CORPUS / "raw" / SOURCE_ID / "sidecar.yaml"
RASTER = Path("/private/tmp/pankratz-1984-usbm-b677-raster")
NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?\Z")
FOOTNOTE = re.compile(r"([*†‡]+)$")
TOC_SECTIONS = (
    ("chapter_1_examples", 1, 46),
    ("elements", 47, 91),
    ("antimonides", 92, 94),
    ("arsenides", 95, 97),
    ("borides", 98, 102),
    ("bromides", 103, 126),
    ("carbides", 127, 139),
    ("carbonates", 140, 143),
    ("chlorides", 144, 178),
    ("fluorides", 179, 217),
    ("hydrides", 218, 227),
    ("iodides", 228, 247),
    ("nitrides", 248, 260),
    ("oxides", 261, 295),
    ("phosphides", 296, 298),
    ("selenides", 299, 306),
    ("silicates", 307, 312),
    ("silicides", 313, 318),
    ("sulfates", 319, 326),
    ("sulfides", 327, 345),
    ("tellurides", 346, 352),
)
GLOBAL_UNITS = (
    "T is in K; Cp° and S° are in cal/mol·K; H°-H°298, ΔHf° and ΔGf° "
    "are in kcal/mol."
)
HEADING_CORRECTIONS = {
    "table-0393": {
        "field": "name_as_published",
        "raw_token": "Lungsten hexabromide (ideal gas)",
        "printed_token": "Tungsten hexabromide (ideal gas)",
        "page": 125,
        "image_quote": "WBr6(g) / Tungsten hexabromide (ideal gas)",
        "reason": "Rendered page proves MinerU confused printed T with L.",
    }
    ,
    "table-1167": {
        "field": "formula_as_published",
        "raw_token": "$\\operatorname{Er}_{2}0_{3}(\\mathbf{c})$",
        "printed_token": "Er2O3",
        "page": 270,
        "image_quote": "Er2O3(c) / Dierbium trioxide",
        "reason": "Rendered page proves MinerU confused printed O with 0.",
    },
    "table-1051": {
        "field": "formula_as_published",
        "raw_token": "$\\mathrm{IiI}_{4}(\\mathrm{g})$",
        "printed_token": "TiI4",
        "page": 245,
        "image_quote": "TiI4(g) / Titanium tetraiodide (ideal gas)",
        "reason": "Rendered page proves MinerU confused the printed Ti prefix with Ii.",
    },
    "table-1439": {
        "field": "formula_as_published",
        "raw_token": "NiSO $_{4}$ (c)",
        "printed_token": "NiSO4",
        "page": 324,
        "image_quote": "NiSO4(c) / Nickel sulfate",
        "reason": "Rendered page proves the subscript belongs to the formula without an embedded space.",
    },
    "table-1301": {
        "field": "formula_as_published",
        "raw_token": "V0(c)",
        "printed_token": "VO",
        "page": 292,
        "image_quote": "VO(c) / Vanadium monoxide",
        "reason": "Rendered page proves MinerU confused the printed O with zero.",
    },
}

COLUMN_HEADING_CORRECTIONS = {
    ("table-0984", 0): {
        "field": "column_heading",
        "raw_token": "I",
        "printed_token": "T",
        "page": 234,
        "image_quote": "T | Cp° | S° | H°-H°298 | ΔHf° | ΔGf°",
        "reason": "Rendered page proves MinerU confused the temperature heading T with I.",
    },
    ("table-1079", 0): {
        "field": "column_heading",
        "raw_token": "I",
        "printed_token": "T",
        "page": 253,
        "image_quote": "T | Cp° | S° | H°-H°298 | ΔHf° | ΔGf°",
        "reason": "Rendered page proves MinerU confused the temperature heading T with I.",
    },
}

IMAGE_VERIFIED_NOTE_RECORDS = {
    "table-0050", "table-0198", "table-0352", "table-0500", "table-0652",
    "table-0800", "table-0951", "table-1100", "table-1251", "table-1400",
}


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"}:
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append("\n")

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.row is not None and self.cell is not None:
            self.row.append(html.unescape("".join(self.cell)).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def parse_table(body: str) -> list[list[str]]:
    parser = TableParser()
    parser.feed(body)
    return parser.rows


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_yaml(path: Path, value) -> None:
    path.write_text(
        yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=110),
        encoding="utf-8",
    )


def load_source_sidecar() -> dict:
    text = SIDECAR.read_text(encoding="utf-8")
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        repaired = re.sub(
            r"^(\s*note:)\s*(.+)$",
            lambda match: f"{match.group(1)} {json.dumps(match.group(2))}",
            text,
            flags=re.MULTILINE,
        )
        return yaml.safe_load(repaired)


def normalize_numeric(raw: str) -> tuple[str, list[str]]:
    token = raw.strip().replace("−", "-").replace("–", "-").replace("—", "-")
    token = token.replace("$", "").replace(" ", "")
    markers: list[str] = []
    match = FOOTNOTE.search(token)
    if match:
        markers.extend(match.group(1))
        token = token[: match.start()]
    sci = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))[x×]10\^?\{?([+-]?\d+)\}?", token)
    if sci:
        token = f"{sci.group(1)}E{sci.group(2)}"
    return token, markers


def parse_number(raw: str) -> tuple[float | None, str, list[str]]:
    token, markers = normalize_numeric(raw)
    return (float(token) if NUMBER.fullmatch(token) else None, token, markers)


def cell(raw: str, image_agreed: bool, source_position: tuple[int, int] | None) -> dict:
    value, numeric_token, markers = parse_number(raw)
    plausible_numeric = bool(re.search(r"\d", raw))
    agreed = value is not None and image_agreed
    suspect = plausible_numeric and (value is None or not agreed)
    result = {
        "raw": raw,
        "value": value,
        "ocr_suspect": suspect,
        "ocr_check": (
            "raster_table_bbox_token_agreement" if agreed else
            "raster_table_bbox_token_disagreement" if plausible_numeric else
            "not_numeric"
        ),
    }
    if numeric_token and numeric_token != raw.strip():
        result["numeric_token"] = numeric_token
    if markers:
        result["footnote_markers"] = markers
    if source_position is not None:
        result["source_row_index"], result["source_column_index"] = source_position
    else:
        result["source_row_index"] = result["source_column_index"] = None
    return result


def visible(text: str) -> str:
    text = re.sub(r"\\mathbf\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\mathrm\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\mathsf\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\text\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"_\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\^\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\(?:operatorname|mathfrak|mathit|text|mathrm|mathsf|mathbf)\s*", "", text)
    text = text.replace("$", "").replace("{", "").replace("}", "")
    text = text.replace("_", "")
    text = text.replace("\\cdot", "·").replace("\\", "")
    return re.sub(r"\s+", " ", text).strip()


def looks_like_formula(text: str) -> bool:
    plain = visible(text)
    return bool(re.search(r"(?:\([^)]*[cglv1][^)]*\)|\[[^]]+\])\s*$", plain, re.I)) and bool(re.search(r"[A-Z]", plain))


def heading(lines: list[str], chapter_two: bool) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    if not chapter_two:
        caption = next((line for line in reversed(lines) if re.match(r"TABLE\s+\d+", line, re.I)), None)
        if caption:
            after = lines[lines.index(caption) + 1:]
            if len(after) >= 2:
                formula_raw, name_raw = after[-2], after[-1]
            else:
                formula_raw = name_raw = None
        else:
            return None, None, None, None, None
    else:
        useful = [line for line in lines if not line.startswith("#")]
        last = useful[-1] if useful else ""
        inline_math = re.match(r"^(\$[^$]+\$)\s+(.+)$", last)
        inline_plain = re.match(r"^(.+?\([^)]*[cglv1][^)]*\))\s+([A-Z].+)$", visible(last), re.I)
        inline_word = re.match(r"^(.+?(?:\d|\)))\s+([A-Z][a-z]{2,}.+)$", visible(last))
        if inline_math and looks_like_formula(inline_math.group(1)):
            formula_raw, name_raw = inline_math.groups()
        elif inline_plain and looks_like_formula(inline_plain.group(1)):
            formula_raw, name_raw = inline_plain.groups()
        elif inline_word:
            formula_raw, name_raw = inline_word.groups()
        elif len(useful) >= 2:
            formula_raw, name_raw = useful[-2], last
        elif looks_like_formula(last):
            formula_raw, name_raw = last, None
        else:
            formula_raw, name_raw = None, last or None
    if not formula_raw:
        return None, visible(name_raw) if name_raw else None, None, None, name_raw
    formula_with_phase = visible(formula_raw)
    phase_match = re.search(r"(\([^)]*\))\s*$", formula_with_phase)
    bracket_match = re.search(r"(\[[^]]*\])\s*$", formula_with_phase)
    state_match = phase_match or bracket_match
    phase = state_match.group(1) if state_match else None
    formula = formula_with_phase[: state_match.start()].strip() if state_match else formula_with_phase
    return formula, visible(name_raw) if name_raw else None, phase, formula_raw, name_raw


def column_unit(heading_raw: str, chapter_two: bool) -> str | None:
    if not chapter_two:
        return None
    plain = visible(heading_raw).lower()
    if plain.strip() in {"t", "i", "t, k"}:
        return "K"
    if "cp" in plain or plain.startswith("s"):
        return "cal/mol·K"
    if "h" in plain or "g" in plain:
        return "kcal/mol"
    return None


def post_table_note_block(text: str, next_chapter_two: bool, has_next_table: bool) -> str:
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    next_caption = None
    if not next_chapter_two:
        next_caption = next((index for index, line in enumerate(lines) if re.match(r"\s*TABLE\s+\d+", line, re.I)), None)
        if next_caption is not None:
            lines = lines[:next_caption]
    meaningful = [index for index, line in enumerate(lines) if line.strip() and not line.lstrip().startswith("#")]
    if has_next_table and next_caption is None and meaningful:
        last = meaningful[-1]
        last_text = lines[last].strip()
        inline_math = re.match(r"^(\$[^$]+\$)\s+(.+)$", last_text)
        inline_plain = re.match(r"^(.+?\([^)]*[cglv1][^)]*\))\s+([A-Z].+)$", visible(last_text), re.I)
        inline_word = re.match(r"^(.+?(?:\d|\)))\s+([A-Z][a-z]{2,}.+)$", visible(last_text))
        inline_identity = (
            (inline_math and looks_like_formula(inline_math.group(1)))
            or (inline_plain and looks_like_formula(inline_plain.group(1)))
            or inline_word
        )
        heading_start = last if inline_identity else (meaningful[-2] if len(meaningful) >= 2 else last)
        if heading_start is not None:
            lines = lines[:heading_start]
    lines = [line for line in lines if not line.lstrip().startswith("#")]
    raw = "\n".join(lines).strip()
    return re.split(r"(?m)^\s*(?:Units:|Sources of data from general references|1\.\s+Barin,)", raw, maxsplit=1)[0].strip()


def parse_note_block(raw: str, image_verified: bool = False) -> dict:
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    estimate_notes = [{"raw": line} for line in lines if re.search(r"\bestimat(?:e|ed|ion)", line, re.I)]
    uncertainties = []
    uncertainty_pattern = re.compile(
        r"(?P<value>\d+(?:\.\d+)?)\s*(?:\\pm|±|\+/-)\s*(?P<uncertainty>\d+(?:\.\d+)?)\s*(?P<unit>[A-Za-z]+)?"
    )
    for line in lines:
        for match in uncertainty_pattern.finditer(line.replace("$", "")):
            uncertainties.append({
                "raw": match.group(0),
                "value": float(match.group("value")),
                "uncertainty": float(match.group("uncertainty")),
                "unit_as_published": match.group("unit"),
                "source_line_raw": line,
            })
    transitions = []
    transition_pattern = re.compile(
        r"(?P<temperature>\d+(?:\.\d+)?)"
        r"(?:\s*\\pm\s*(?P<temperature_uncertainty>\d+(?:\.\d+)?))?"
        r"\s*(?:K|\\mathrm\{~?K\})\s*,\s*"
        r"(?P<kind>transition|melting|boiling|sublimation|decomposition|fusion|vaporization)"
        r"(?:\s+point|\s+to\b)",
        re.I,
    )
    enthalpy_pattern = re.compile(
        r"=\s*(?P<enthalpy>[+-]?\d+(?:\.\d+)?)"
        r"(?:\s*\\pm\s*(?P<enthalpy_uncertainty>\d+(?:\.\d+)?))?"
    )
    for line in lines:
        plain_line = line.replace("$", "")
        for match in transition_pattern.finditer(plain_line):
            tail = plain_line[match.end():]
            enthalpy = enthalpy_pattern.search(tail)
            transitions.append({
                "raw": line,
                "temperature_K": float(match.group("temperature")),
                "temperature_uncertainty_K": (
                    float(match.group("temperature_uncertainty"))
                    if match.group("temperature_uncertainty") else None
                ),
                "kind": match.group("kind").lower(),
                "enthalpy_as_published": float(enthalpy.group("enthalpy")) if enthalpy else None,
                "enthalpy_uncertainty_as_published": (
                    float(enthalpy.group("enthalpy_uncertainty"))
                    if enthalpy and enthalpy.group("enthalpy_uncertainty") else None
                ),
            })
    return {
        "raw": raw,
        "lines_raw": lines,
        "ocr_suspect": bool(raw) and not image_verified,
        "ocr_check": (
            "image_verified_fixture_agreement" if raw and image_verified else
            "mineru_post_table_text_retained_not_image_verified" if raw else
            "no_post_table_text"
        ),
        "ambiguities": ([{
            "kind": "post_table_note_ocr_unverified",
            "reason": "Raw MinerU note text is retained verbatim; no image correction is asserted.",
        }] if raw and not image_verified else []),
        "corrections": [],
        "estimate_notes": estimate_notes,
        "uncertainties": uncertainties,
        "transitions": transitions,
    }


def section_for(page: int) -> str:
    for name, first, last in TOC_SECTIONS:
        if first <= page <= last:
            return name
    return "outside_toc_numeric_tables"


def load_source_tables() -> list[dict]:
    tables: list[dict] = []
    source_dir = DEST / "source/mineru"
    source_dir.mkdir(parents=True, exist_ok=True)
    for directory in sorted(MINERU.glob("chunk-*")):
        start = int(re.search(r"p(\d+)", directory.name).group(1))
        markdown_path = next(directory.glob("*.md"))
        copied = source_dir / markdown_path.name
        shutil.copyfile(markdown_path, copied)
        markdown = markdown_path.read_text(encoding="utf-8")
        matches = list(re.finditer(r"<table>[\s\S]*?</table>", markdown))
        content_path = next(path for path in directory.glob("*_content_list.json") if "_v2" not in path.name)
        content = json.loads(content_path.read_text(encoding="utf-8"))
        items = [item for item in content if item.get("type") == "table"]
        if len(matches) != len(items):
            raise ValueError(f"table count mismatch in {directory.name}: {len(matches)} != {len(items)}")
        prior = 0
        for index, (match, item) in enumerate(zip(matches, items, strict=True), 1):
            if parse_table(match.group()) != parse_table(item["table_body"]):
                raise ValueError(f"markdown/content-list disagreement: {directory.name} table {index}")
            context = [line.strip() for line in markdown[prior:match.start()].splitlines() if line.strip()]
            next_start = matches[index].start() if index < len(matches) else len(markdown)
            next_pdf_page = start + int(items[index]["page_idx"]) if index < len(items) else None
            note_raw = post_table_note_block(
                markdown[match.end():next_start],
                bool(next_pdf_page is not None and next_pdf_page - 4 >= 47),
                index < len(matches),
            ) if start + int(item["page_idx"]) - 4 >= 47 else ""
            tables.append({
                "chunk": directory.name,
                "source_file": copied.name,
                "source_table_index": index,
                "html": match.group(),
                "context": context[-12:],
                "post_table_note_block_raw": note_raw,
                "pdf_page": start + int(item["page_idx"]),
                "bbox": item.get("bbox"),
            })
            prior = match.end()
    return tables


def render_and_ocr(pages: set[int]) -> dict[int, dict]:
    RASTER.mkdir(parents=True, exist_ok=True)
    result = {}
    for page in sorted(pages):
        image = RASTER / f"page-{page:03}.png"
        text_path = RASTER / f"page-{page:03}"
        tsv_file = text_path.with_suffix(".tsv")
        if not image.exists():
            subprocess.run(
                ["pdftoppm", "-f", str(page), "-l", str(page), "-r", "180", "-singlefile", "-png", str(PDF), str(image.with_suffix(""))],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        if not tsv_file.exists():
            subprocess.run(
                ["tesseract", str(image), str(text_path), "--psm", "6", "tsv"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        with image.open("rb") as stream:
            header = stream.read(24)
        width, height = struct.unpack(">II", header[16:24])
        words = []
        for line in tsv_file.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
            fields = line.split("\t")
            if len(fields) == 12 and fields[11].strip():
                words.append((int(fields[6]), int(fields[7]), int(fields[8]), int(fields[9]), fields[11].strip()))
        result[page] = {"width": width, "height": height, "words": words}
        print(f"STATUS: {SOURCE_ID} raster page {page}", flush=True)
    return result


def table_image_words(scan: dict, bbox: list[int] | None) -> list[str]:
    if not bbox:
        return []
    x1, y1, x2, y2 = bbox
    xscale = scan["width"] / 1000.0
    yscale = scan["height"] / 1000.0
    margin = 8
    selected = []
    for left, top, width, height, text in scan["words"]:
        cx = (left + width / 2) / xscale
        cy = (top + height / 2) / yscale
        if x1 - margin <= cx <= x2 + margin and y1 - margin <= cy <= y2 + margin:
            selected.append((top, left, text))
    return [text for _, _, text in sorted(selected)]


def raster_region_words(scan: dict, bbox: list[int] | None, top_offset: int, bottom_offset: int) -> list[str]:
    if not bbox:
        return []
    x1, y1, x2, _ = bbox
    xscale = scan["width"] / 1000.0
    yscale = scan["height"] / 1000.0
    selected = []
    for left, top, width, height, value in scan["words"]:
        cx = (left + width / 2) / xscale
        cy = (top + height / 2) / yscale
        if x1 - 25 <= cx <= x2 + 25 and y1 + top_offset <= cy <= y1 + bottom_offset:
            selected.append((top, left, value))
    return [value for _, _, value in sorted(selected)]


def raster_column_header_words(scan: dict, bbox: list[int] | None, index: int, count: int) -> list[str]:
    if not bbox or count <= 0:
        return []
    x1, y1, x2, _ = bbox
    column_width = (x2 - x1) / count
    narrowed = [x1 + index * column_width, y1, x1 + (index + 1) * column_width, y1]
    return raster_region_words(scan, narrowed, -5, 65)


def image_token_agrees(value: str | None, words: list[str]) -> bool:
    if not value:
        return False
    expected = re.sub(r"[^a-z0-9]+", "", visible(value).lower())
    raster_text = re.sub(r"[^a-z0-9]+", "", " ".join(words).lower())
    return bool(expected and expected in raster_text)


def heading_token(
    raw: str | None,
    value: str | None,
    words: list[str],
    corrected: bool = False,
    evidence_value: str | None = None,
) -> dict:
    agreed = image_token_agrees(evidence_value or value, words)
    return {
        "raw": raw or "",
        "value": value,
        "ocr_suspect": corrected or not agreed,
        "ocr_check": (
            "image_proven_correction" if corrected else
            "raster_heading_region_token_agreement" if agreed else
            "raster_heading_region_token_disagreement"
        ),
    }


def source_cell_agreements(source_rows: list[list[str]], scan: dict, bbox: list[int] | None) -> set[tuple[int, int]]:
    source = []
    for row_index, row in enumerate(source_rows):
        for column_index, raw in enumerate(row):
            value, token, _ = parse_number(raw)
            if value is not None:
                source.append(((row_index, column_index), token))
    raster = []
    for word in table_image_words(scan, bbox):
        for raw in re.findall(r"[+\-−–—]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+\-]?\d+)?(?:[*†‡])?", word):
            token = normalize_numeric(raw)[0]
            if token:
                raster.append(token)
    matcher = SequenceMatcher(a=[token for _, token in source], b=raster, autojunk=False)
    agreed = set()
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            agreed.add(source[block.a + offset][0])
    return agreed


def identity_disagreements(rows: list[dict], record_id: str, page: int) -> list[dict]:
    issues = []
    if page < 47:
        return issues
    for index, row in enumerate(rows):
        cells = row["cells"]
        if len(cells) >= 4 and cells[0]["value"] == 298.0 and cells[3]["value"] not in {None, 0.0}:
            issues.append({
                "kind": "identity_disagreement",
                "record_id": record_id,
                "page": page,
                "row_index": index,
                "detector": "H(298)-H(298) must equal zero",
                "raw_tokens": [cells[0]["raw"], cells[3]["raw"]],
            })
        if index == 0 or len(cells) < 4:
            continue
        previous = rows[index - 1]["cells"]
        values = [previous[0]["value"], cells[0]["value"], previous[1]["value"], cells[1]["value"], previous[3]["value"], cells[3]["value"]]
        if any(value is None for value in values) or values[1] <= values[0]:
            continue
        predicted = 0.5 * (values[2] + values[3]) * (values[1] - values[0]) / 1000.0
        observed = values[5] - values[4]
        if abs(predicted - observed) > max(0.5, 0.20 * max(abs(observed), 0.1)):
            issues.append({
                "kind": "identity_disagreement",
                "record_id": record_id,
                "page": page,
                "row_index": index,
                "detector": "integral Cp dT approximately equals enthalpy-increment change",
                "predicted_kcal_per_mol": round(predicted, 6),
                "observed_kcal_per_mol": round(observed, 6),
                "raw_tokens": [previous[0]["raw"], cells[0]["raw"], previous[1]["raw"], cells[1]["raw"], previous[3]["raw"], cells[3]["raw"]],
            })
    return issues


def build_records(tables: list[dict], raster: dict[int, str]) -> tuple[list[dict], list[dict]]:
    records_dir = DEST / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    records = []
    manifest_entries = []
    for ordinal, table in enumerate(tables, 1):
        record_id = f"table-{ordinal:04d}"
        page = table["pdf_page"] - 4
        chapter_two = page >= 47
        parsed = parse_table(table["html"])
        source_rows = parsed
        positions = [[(row_index, column_index) for column_index in range(len(row))] for row_index, row in enumerate(parsed)]
        transposed = (
            len(parsed) == 2
            and all(len(row) == len(parsed[0]) for row in parsed)
            and len(parsed[0]) > 1
            and all(parse_number(parsed[row][0])[0] is None for row in range(2))
            and all(parse_number(parsed[row][column])[0] is not None for row in range(2) for column in range(1, len(parsed[0])))
        )
        if transposed:
            original = parsed
            parsed = [[original[1][0], original[0][0]]] + [[original[1][column], original[0][column]] for column in range(1, len(original[0]))]
            positions = [[(1, 0), (0, 0)]] + [[(1, column), (0, column)] for column in range(1, len(original[0]))]
            data_start = 1
        else:
            data_start = next(
                (i for i, row in enumerate(parsed) if sum(parse_number(raw)[0] is not None for raw in row) >= 2),
                len(parsed),
            )
        width = max((len(row) for row in parsed), default=0)
        header_rows = [row + [""] * (width - len(row)) for row in parsed[:data_start]]
        headings = [" / ".join(row[i] for row in header_rows if row[i]).strip() for i in range(width)]
        corrections = []
        columns = []
        for column_index, raw_heading in enumerate(headings):
            published_heading = raw_heading
            correction = COLUMN_HEADING_CORRECTIONS.get((record_id, column_index))
            if correction:
                correction = {"record_id": record_id, "column_index": column_index, **correction}
                if raw_heading != correction["raw_token"]:
                    raise ValueError(f"{record_id} column correction raw token drifted")
                published_heading = correction["printed_token"]
                corrections.append(correction)
            columns.append({
                "index": column_index,
                "heading_raw": raw_heading,
                "heading_as_published": published_heading,
                "units_as_published": column_unit(published_heading, chapter_two),
                "heading_token": heading_token(
                    raw_heading,
                    published_heading,
                    raster_column_header_words(
                        raster[table["pdf_page"]], table["bbox"], column_index, width
                    ),
                    bool(correction),
                ),
            })
        image_agreements = source_cell_agreements(source_rows, raster[table["pdf_page"]], table["bbox"])
        rows = []
        for source_row_index, source_row in enumerate(parsed[data_start:], data_start):
            padded = source_row + [""] * (width - len(source_row))
            source_positions = positions[source_row_index] + [None] * (width - len(positions[source_row_index]))
            rows.append({
                "source_row_index": source_row_index,
                "cells": [cell(raw, position in image_agreements if position is not None else False, position) for raw, position in zip(padded, source_positions, strict=True)],
            })
        formula, name, phase, formula_raw, name_raw = heading(table["context"], chapter_two)
        table_number_match = next((re.search(r"TABLE\s+(\d+)", line, re.I) for line in reversed(table["context"]) if re.search(r"TABLE\s+\d+", line, re.I)), None)
        if record_id in HEADING_CORRECTIONS:
            correction = {"record_id": record_id, **HEADING_CORRECTIONS[record_id]}
            if correction["field"] == "name_as_published" and name == correction["raw_token"]:
                name = correction["printed_token"]
                corrections.append(correction)
            elif correction["field"] == "formula_as_published" and formula_raw == correction["raw_token"]:
                formula = correction["printed_token"]
                corrections.append(correction)
            else:
                raise ValueError(f"{record_id} heading correction raw token drifted")
        identity_words = raster_region_words(raster[table["pdf_page"]], table["bbox"], -140, -3)
        formula_corrected = any(item["field"] == "formula_as_published" for item in corrections)
        name_corrected = any(item["field"] == "name_as_published" for item in corrections)
        formula_phase_evidence = f"{formula or ''}{phase or ''}"
        formula_state = heading_token(
            formula_raw, formula, identity_words, formula_corrected, formula_phase_evidence
        )
        name_state = heading_token(name_raw, name, identity_words, name_corrected)
        phase_state = heading_token(phase, phase, identity_words, False, formula_phase_evidence)
        note_block = parse_note_block(
            table["post_table_note_block_raw"], record_id in IMAGE_VERIFIED_NOTE_RECORDS
        )
        ambiguities = []
        corrected_fields = {(item["field"], item.get("column_index")) for item in corrections}
        for field, state in (("formula_as_published", formula_state), ("name_as_published", name_state), ("phase_as_published", phase_state)):
            if state["ocr_suspect"] and (field, None) not in corrected_fields:
                ambiguities.append({
                    "kind": "ocr_heading_unresolved",
                    "field": field,
                    "raw_token": state["raw"],
                    "reason": "Automated comparison with the rendered page heading region did not agree; no correction inferred.",
                })
        for column in columns:
            if column["heading_token"]["ocr_suspect"] and ("column_heading", column["index"]) not in corrected_fields:
                ambiguities.append({
                    "kind": "ocr_heading_unresolved",
                    "field": "column_heading",
                    "column_index": column["index"],
                    "raw_token": column["heading_raw"],
                    "reason": "Automated comparison with the rendered table-heading region did not agree; no correction inferred.",
                })
        if data_start == len(parsed):
            ambiguities.append({"kind": "untranscribed", "reason": "MinerU table contains no recoverable numeric data row"})
        if any(len(row) != width for row in parsed):
            ambiguities.append({"kind": "column_shape", "reason": "MinerU rows have unequal cell counts; missing cells retained as blank padding", "row_lengths": [len(row) for row in parsed]})
        identity = identity_disagreements(rows, record_id, page)
        ambiguities.extend(identity)
        transcription_status = "untranscribed" if data_start == len(parsed) else ("transcribed_with_ocr_ambiguities" if ambiguities else "transcribed")
        record = {
            "schema_version": "pankratz_b677_native_table.v1",
            "record_id": record_id,
            "ordinal": ordinal,
            "formula_as_published": formula,
            "formula_heading_raw": formula_raw,
            "name_as_published": name,
            "name_heading_raw": name_raw,
            "phase_as_published": phase,
            "formula_token": {
                **formula_state,
            },
            "name_token": name_state,
            "phase_token": phase_state,
            "page": page,
            "pdf_page": table["pdf_page"],
            "table_number": table_number_match.group(1) if table_number_match else None,
            "toc_section": section_for(page),
            "table_kind": "thermodynamic_temperature_grid" if rows else "numeric_table_untranscribed",
            "transcription_status": transcription_status,
            "units_statement_as_published": GLOBAL_UNITS if chapter_two else None,
            "columns": columns,
            "header_rows_raw": header_rows,
            "source_table_rows_raw": source_rows,
            "source_orientation": "transposed_for_temperature_lookup" if transposed else "rows_as_printed",
            "temperature_grid": [row["cells"][0] for row in rows if row["cells"]],
            "rows": rows,
            "footnote_markers": sorted({marker for row in rows for item in row["cells"] for marker in item.get("footnote_markers", [])}),
            "post_table_note_block": note_block,
            "source_locator": {
                "file": f"source/mineru/{table['source_file']}",
                "table_index": table["source_table_index"],
                "table_sha256": hashlib.sha256(table["html"].encode()).hexdigest(),
                "note_block_sha256": hashlib.sha256(table["post_table_note_block_raw"].encode()).hexdigest(),
                "complete_source_block_sha256": hashlib.sha256(
                    (table["html"] + "\n" + table["post_table_note_block_raw"]).encode()
                ).hexdigest(),
                "bbox": table["bbox"],
            },
            "ambiguities": ambiguities,
            "corrections": corrections,
        }
        path = records_dir / f"{record_id}.json"
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        numeric = [item for row in rows for item in row["cells"] if item["value"] is not None]
        suspects = [item for row in rows for item in row["cells"] if item["ocr_suspect"]]
        manifest_entries.append({
            "record_id": record_id,
            "formula": formula,
            "phase": phase,
            "name_as_published": name,
            "path": str(path.relative_to(DEST)),
            "source_locator": record["source_locator"],
            "page": page,
            "pdf_page": table["pdf_page"],
            "table_number": record["table_number"],
            "toc_section": record["toc_section"],
            "row_count": len(rows),
            "numeric_cell_count": len(numeric),
            "ocr_suspect_count": len(suspects),
            "heading_ocr_suspect_count": sum(
                token["ocr_suspect"] for token in (formula_state, name_state, phase_state)
            ) + sum(column["heading_token"]["ocr_suspect"] for column in columns),
            "post_table_note_block_present": bool(note_block["raw"]),
            "ambiguity_count": len(ambiguities),
            "ambiguities": ambiguities,
            "transcription_status": transcription_status,
        })
        records.append(record)
        if ordinal % 50 == 0 or ordinal == len(tables):
            print(f"STATUS: {SOURCE_ID} table {ordinal:04d}/{len(tables)} page {page}", flush=True)
    return records, manifest_entries


def build_census(entries: list[dict]) -> dict:
    section_counts = Counter(entry["toc_section"] for entry in entries)
    sections = [
        {"section": name, "printed_page_range": [first, last], "pdf_page_range": [first + 4, last + 4], "record_count": section_counts[name]}
        for name, first, last in TOC_SECTIONS
    ]
    return {
        "basis": "Bulletin CONTENTS section starts and the next section boundary; every MinerU numeric table in each resulting printed-page range was counted.",
        "record_count": len(entries),
        "record_ids": [entry["record_id"] for entry in entries],
        "sections": sections,
        "found_count": len(entries),
        "transcribed_count": sum(entry["transcription_status"] != "untranscribed" for entry in entries),
        "untranscribed_count": sum(entry["transcription_status"] == "untranscribed" for entry in entries),
    }


def update_access_status(pdf_sha: str, record_count: int) -> None:
    path = ROOT / "data/literature/compilations/access-status.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["updated"] = "2026-09-07"
    data["sources"]["pankratz_1984_usbm_b677"] = {
        "citation": "Pankratz, L. B., Stuve, J. M. and Gokcen, N. A., 1984, Thermodynamic Data for Mineral Technology, U.S. Bureau of Mines Bulletin 677, 360 pp.",
        "report_number": "USBM Bulletin 677",
        "access": "public_domain_us_government_work",
        "official_url": "https://digital.library.unt.edu/ark:/67531/metadc12819/",
        "retrieved_url": "https://web.archive.org/web/20251020214103id_/https://digital.library.unt.edu/ark:/67531/metadc12819/m2/1/high_res_d/Bulletin0677.pdf",
        "licence_basis": "United States government work. U.S. Bureau of Mines bulletin; public domain in the United States.",
        "access_date": "2026-09-07",
        "local_status": "complete_ingest",
        "harvested_path": f"data/literature/compilations/{SOURCE_ID}/",
        "harvested_record_count": record_count,
        "source_pdf_sha256": pdf_sha,
        "note": "Assessed thermodynamic reference tables; not measurements and never battery-scored.",
    }
    write_yaml(path, data)


def main() -> None:
    sidecar = load_source_sidecar()
    pdf_sha = sha256(PDF)
    if pdf_sha != sidecar["sha256"]:
        raise ValueError("PDF checksum differs from corpus sidecar")
    DEST.mkdir(parents=True, exist_ok=True)
    tables = load_source_tables()
    if len(tables) != 1571:
        raise ValueError(f"expected MinerU decode census 1571, got {len(tables)}")
    raster = render_and_ocr({table["pdf_page"] for table in tables})
    records, entries = build_records(tables, raster)
    census = build_census(entries)
    (DEST / "census.json").write_text(json.dumps(census, indent=2) + "\n", encoding="utf-8")
    numeric_cells = [item for record in records for row in record["rows"] for item in row["cells"] if item["value"] is not None]
    suspects = [item for record in records for row in record["rows"] for item in row["cells"] if item["ocr_suspect"]]
    identity = [item for record in records for item in record["ambiguities"] if item.get("kind") == "identity_disagreement"]
    corrections = [item for record in records for item in record["corrections"]]
    heading_suspects = [
        token
        for record in records
        for token in (
            record["formula_token"], record["name_token"], record["phase_token"],
            *(column["heading_token"] for column in record["columns"]),
        )
        if token["ocr_suspect"]
    ]
    note_records = [record for record in records if record["post_table_note_block"]["raw"]]
    uncertainties = [
        item for record in note_records for item in record["post_table_note_block"]["uncertainties"]
    ]
    transitions = [
        item for record in note_records for item in record["post_table_note_block"]["transitions"]
    ]
    estimate_notes = [
        item for record in note_records for item in record["post_table_note_block"]["estimate_notes"]
    ]
    source = {
        "database": "Thermodynamic Data for Mineral Technology",
        "authors": "Pankratz, L. B.; Stuve, J. M.; Gokcen, N. A.",
        "version": "USBM Bulletin 677 (1984)",
        "date_as_published": "1984",
        "official_url": "https://digital.library.unt.edu/ark:/67531/metadc12819/",
        "licence": "United States government work; public domain in the United States.",
        "access_date": "2026-09-07",
    }
    manifest = {
        "schema_version": "literature_compilation_manifest.v1",
        "source_id": SOURCE_ID,
        "source": source,
        "compilation_role": {
            "engine_reference_input": True,
            "validation_measurement": False,
            "scoring_eligible": False,
            "battery_refusal": "assessed_compilation_not_runtime_observable",
        },
        "source_files": [{"path": str(PDF), "sha256": pdf_sha, "storage": "external read-only regolith corpus"}],
        "census": census,
        "summary": {
            "record_count": len(records),
            "transcribed_record_count": census["transcribed_count"],
            "untranscribed_record_count": census["untranscribed_count"],
            "row_count": sum(len(record["rows"]) for record in records),
            "parsed_numeric_cell_count": len(numeric_cells),
            "ocr_suspect_count": len(suspects),
            "heading_ocr_suspect_count": len(heading_suspects),
            "identity_check_disagreement_count": len(identity),
            "correction_count": len(corrections),
            "post_table_note_record_count": len(note_records),
            "estimate_note_count": len(estimate_notes),
            "uncertainty_count": len(uncertainties),
            "transition_count": len(transitions),
        },
        "ocr_policy": {
            "primary_reading": "MinerU markdown and table HTML from nine local decode chunks",
            "second_reading": "Occurrence-preserving sequence alignment of Tesseract word-box tokens inside each MinerU table rectangle on 180-dpi original-PDF renders",
            "heading_reading": "Every formula, name, phase, and column heading is compared with Tesseract word boxes from its rendered page region; disagreement is suspect and unresolved unless a page-image correction is ledgered.",
            "numeric_rule": "Preserve raw printed/OCR token; parse only syntactically numeric tokens; any raster disagreement remains ocr_suspect and is never corrected.",
            "identity_checks": "Detector only; no value correction.",
        },
        "ocr_suspect_examples": [
            {"record_id": record["record_id"], "page": record["page"], "tokens": [item["raw"] for row in record["rows"] for item in row["cells"] if item["ocr_suspect"]][:5]}
            for record in records if any(item["ocr_suspect"] for row in record["rows"] for item in row["cells"])
        ][:20],
        "identity_check_disagreements": identity,
        "corrections": corrections,
        "feedstock_element_coverage": feedstock_coverage([{"formula": record.get("formula_as_published") or ""} for record in records]),
        "untranscribed": [
            {"record_id": entry["record_id"], "page_range": [entry["page"], entry["page"]], "reason": entry["ambiguities"]}
            for entry in entries if entry["transcription_status"] == "untranscribed"
        ],
        "entries": [{**entry, "source": source, "sha256": pdf_sha, "original_record_text_location": entry["source_locator"]} for entry in entries],
    }
    write_yaml(DEST / "manifest.yaml", manifest)
    write_yaml(DEST / "source/sidecar.yaml", sidecar)
    update_access_status(pdf_sha, len(records))
    print(json.dumps(manifest["summary"], sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
