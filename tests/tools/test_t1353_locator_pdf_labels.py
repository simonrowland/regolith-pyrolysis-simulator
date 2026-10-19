"""Check changed table/figure locators against their cited physical PDF pages."""
from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path

import yaml

BASE = "28af098ed04b8ca99f569381a2f38330cb66d7cd"
ROOT = Path(__file__).resolve().parents[2]
CORPUS_ROOTS = (
    Path("/Users/simonrowland/Repos/regolith-corpus/raw"),
    Path("/Users/simonrowland/Repos/.regolith-main-review/corpus-main/raw"),
)
TOUCHED_EXTRACTS = (
    "data/literature/extracts/adamkovicova-1980-casio3-heat-of-fusion.yaml",
    "data/literature/extracts/basu-2008-feo-steelmaking.yaml",
    "data/literature/extracts/boulliung-wood-2022-so2-solubility-silicate-melts.yaml",
    "data/literature/extracts/busemann-2000-phase-q-noble-gases.yaml",
    "data/literature/extracts/ivtan-cro2-g.yaml",
    "data/literature/extracts/kems-003-pound-1972.yaml",
    "data/literature/extracts/kems-005-fedkin-2006.yaml",
    "data/literature/extracts/kems-019-miller-armatys-2013.yaml",
    "data/literature/extracts/kems-020-hastie-1981-nbsir.yaml",
    "data/literature/extracts/kems-021-plante-1992-feo.yaml",
    "data/literature/extracts/kems-027-plante-hastie-1983.yaml",
    "data/literature/extracts/kems-031-halwax-2024.yaml",
    "data/literature/extracts/kems-036-sesko-2024.yaml",
    "data/literature/extracts/kems-041-sossi-fegley-2018.yaml",
    "data/literature/extracts/kems-053-stolyarova-1991.yaml",
    "data/literature/extracts/kems-114-nichols-1995.yaml",
    "data/literature/extracts/kems-119-furukawa-1975.yaml",
    "data/literature/extracts/kems-137-bischof-2023.yaml",
    "data/literature/extracts/kems-139-jacobson-2024.yaml",
    "data/literature/extracts/kems-189-bischof-2021.yaml",
    "data/literature/extracts/kume-2000-cao-activities.yaml",
    "data/literature/extracts/lpsc-2017-2171.yaml",
    "data/literature/extracts/ohta-1996-cao-mgo-al2o3-activities.yaml",
    "data/literature/extracts/ohta-suito-1995-mno-activity-slags.yaml",
    "data/literature/extracts/oneill-2002-feo-activity-coefficients-cmas.yaml",
    "data/literature/extracts/reiss-2019-thermal-extraction-nulht2m.yaml",
    "data/literature/extracts/robinot-2025-promes-review.yaml",
    "data/literature/extracts/schaefer-and-fegley-2007-icarus-outgassing-of-oc.yaml",
    "data/literature/extracts/seki-2011-ca-o-solubility-fe-cao-al2o3.yaml",
    "data/literature/extracts/shornikov-1994-mullite-kems.yaml",
    "data/literature/extracts/shornikov-1997-diopside-vaporization.yaml",
    "data/literature/extracts/shornikov-2008-redox-oxide-evaporation.yaml",
    "data/literature/extracts/shornikov-2018-spinel-solid-solutions.yaml",
    "data/literature/extracts/shornikov-2021-mgo-feo-silicate.yaml",
    "data/literature/extracts/shornikov-2023-calcium-phosphate.yaml",
    "data/literature/extracts/shornikov-2025-saratov-alkali-evaporation.yaml",
    "data/literature/extracts/shults-1994-mullite-evaporation.yaml",
    "data/literature/extracts/slag-002-banya-hino-nagasaka-1993.yaml",
    "data/literature/extracts/slag-003-hino-kitagawa-banya-1993.yaml",
    "data/literature/extracts/stolyarova-1996-cao-alumina-silica-kems.yaml",
    "data/literature/extracts/ts1985.yaml",
    "data/literature/extracts/yam1983.yaml",
    "data/literature/extracts/zigo-1987-gehlenite-fusion.yaml",
)


def _walk_locators(value, path=()):
    if isinstance(value, dict):
        if ("table" in value or "figure" in value) and (
            "page" in value or "pdf_page_index" in value
        ):
            yield path, value
        for key, child in value.items():
            yield from _walk_locators(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_locators(child, path + (str(index),))


def _yaml(text):
    return yaml.safe_load(text) or {}


def _changed_extract_paths():
    return TOUCHED_EXTRACTS


def _pdf_path(source_id, corpus_source_id):
    for sid in dict.fromkeys((source_id, corpus_source_id)):
        if not sid:
            continue
        for root in CORPUS_ROOTS:
            candidate = root / sid / f"{sid}.pdf"
            if candidate.is_file():
                return candidate
    return None


def _label_pattern(locator):
    kind, label = next((key, locator[key]) for key in ("table", "figure") if key in locator)
    label = str(label).strip().strip("'\"")
    label = re.sub(r"\s*\(continued\)$", "", label, flags=re.I)
    if kind == "figure":
        if re.fullmatch(r"\d+-\d+", label):
            first, last = label.split("-")
            return re.compile(rf"\b(?:fig(?:ure)?\.?\s*(?:{first}\s*[-–]\s*{last}|[{first}-{last}])|図\s*[{first}-{last}])\b", re.I)
        if label == "10":
            label = r"(?:10|1O|l0|lO)"
        if re.fullmatch(r"\d+[A-Z]", label):
            number, panel = label[:-1], label[-1]
            return re.compile(
                rf"(?:\bfig(?:ure)?\.?\s*{number}\s*{panel}\b|"
                rf"\bfig(?:ure)?\.?\s*{number}\b.{{0,240}}?\(\s*{panel}\s*\))",
                re.I | re.S,
            )
        figure_label = label if label.startswith("(?:") or r"\s" in label else re.escape(label)
        return re.compile(
            rf"(?:\bfig(?:ure)?\.?\s*{figure_label}\b|図\s*{figure_label}\b|^\s*{re.escape(label)}\.)",
            re.I | re.M,
        )
    if label.lower() == "ia":
        return re.compile(r"\btable\s+(?:ia|la)\b", re.I)
    if label == "1":
        return re.compile(r"\b(?:table|таблица)\s*(?:1|i|l)\b", re.I)
    label_patterns = []
    for part in re.split(r"\s*,\s*", label):
        label_pattern = re.escape(part)
        label_pattern = re.sub(r"I", "[I1l]", label_pattern, flags=re.I)
        label_patterns.append(label_pattern)
    return re.compile(rf"\b(?:table|таблица|表)\s*(?:{'|'.join(label_patterns)})\b", re.I)


def _normalize_label_text(text):
    return re.sub(
        r"\b(?:[A-Z]\s+){2,}[A-Z](?:\s+[0-9]){1,}\b",
        lambda match: re.sub(r"\s+", "", match.group()),
        text,
        flags=re.I,
    )


@lru_cache(maxsize=None)
def _page_text(pdf, page_number):
    result = subprocess.run(
        ["pdftotext", "-f", str(page_number), "-l", str(page_number), "-layout", pdf, "-"],
        check=True, capture_output=True, text=True, timeout=30,
    )
    return result.stdout


def _has_page_text(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    substantive = [
        line for line in lines
        if not re.fullmatch(r"\d{4}M&PS\.\.\..*", line)
        and not re.fullmatch(r"\d+", line)
    ]
    return bool(substantive)


def _assert_label_and_data(locator, page_text, previous_page_text=None):
    label_pattern = _label_pattern(locator)
    if locator.get("table_continued") is True:
        if previous_page_text is None or not label_pattern.search(previous_page_text):
            raise AssertionError("continued table caption is absent from previous page")
        match = re.search(r"data cell:\s*([^.;]+)", locator.get("note", ""), re.I)
        if match is None:
            raise AssertionError("continued table locator requires a data-cell record in its note")
        record = match.group(1)
        record_pattern = re.compile(r"\s+".join(re.escape(part) for part in record.split()))
        if not record_pattern.search(page_text):
            raise AssertionError("continued table data-cell record is absent from cited page")
        return
    if not label_pattern.search(page_text):
        raise AssertionError("table/figure label is absent from cited page")


def test_changed_table_and_figure_locators_name_their_physical_pdf_page(capsys):
    checked = skipped = 0
    failures = []
    for relative in _changed_extract_paths():
        current_path = ROOT / relative
        current = _yaml(current_path.read_text())
        old = subprocess.run(
            ["git", "show", f"{BASE}:{relative}"], cwd=ROOT,
            check=True, capture_output=True, text=True,
        ).stdout
        before = dict(_walk_locators(_yaml(old)))
        after = dict(_walk_locators(current))
        pdf = _pdf_path(current.get("source_id"), current.get("source", {}).get("corpus_source_id"))
        for locator_path, locator in after.items():
            prior = before.get(locator_path)
            if prior is None or not any(prior.get(key) != locator.get(key)
                                        for key in ("page", "published_page", "pdf_page_index", "table_continued")):
                continue
            if not ("page" in locator and "pdf_page_index" in locator):
                continue
            label = f"{next(k for k in ('table', 'figure') if k in locator)} {locator[next(k for k in ('table', 'figure') if k in locator)]}"
            if pdf is None:
                skipped += 1
                continue
            page_number = int(locator["pdf_page_index"]) + 1
            text = _page_text(str(pdf), page_number)
            if not _has_page_text(text):
                skipped += 1
                continue
            previous_text = (
                _page_text(str(pdf), page_number - 1)
                if locator.get("table_continued") is True and page_number > 1
                else None
            )
            try:
                _assert_label_and_data(
                    locator,
                    _normalize_label_text(text),
                    _normalize_label_text(previous_text) if previous_text is not None else None,
                )
            except AssertionError as exc:
                failures.append(f"{relative} {locator_path}: {label} on PDF page {page_number}: {exc}")
                continue
            checked += 1
    print(f"t1353 PDF locator labels: {checked} passed, {skipped} skipped, {len(failures)} failed")
    assert checked > 0, "no locator labels were checked"
    assert failures == [], "PDF locator label failures:\n" + "\n".join(failures)
    captured = capsys.readouterr()
    print(captured.out, end="")


def test_continued_table_checks_caption_on_previous_page_and_data_on_cited_page():
    locator = {
        "table": "877",
        "table_continued": True,
        "note": "Table 877 continues; data cell: 3300.000 64.806.",
    }
    _assert_label_and_data(
        locator,
        "T (K)  F°(T)\n3300.000 64.806 346.974",
        "Class accuracy\nTable 877",
    )
