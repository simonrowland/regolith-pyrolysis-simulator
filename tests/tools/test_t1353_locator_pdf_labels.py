"""Check changed table/figure locators against their cited physical PDF pages."""
from __future__ import annotations

import os
import re
import subprocess
import csv
import json
import unicodedata
from functools import lru_cache
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

BASE = "28af098ed04b8ca99f569381a2f38330cb66d7cd"
ROOT = Path(__file__).resolve().parents[2]
CORPUS_ROOTS = tuple(
    Path(corpus_root) / "raw"
    for corpus_root in dict.fromkeys(
        (
            os.environ.get("REGOLITH_CORPUS_ROOT"),
            str(Path.home() / "Repos/regolith-corpus"),
        )
    )
    if corpus_root
)
MINERU_TEXT_ROOT = Path.home() / "Repos/.regolith-main-review/corpus-main/text"
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
        if label.isdigit() or label.startswith("(?:"):
            figure_label = label if label.startswith("(?:") else re.escape(label)
            return re.compile(
                rf"(?:\bfig(?:ure)?\s*\.?\s*{figure_label}(?![0-9A-Za-z])|"
                rf"\b図\s*{figure_label}(?![0-9A-Za-z])|"
                rf"^\s*{re.escape(label)}\.)",
                re.I | re.M,
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
    text = unicodedata.normalize("NFKC", text)
    return re.sub(
        r"\b(?:[A-Z]\s+){2,}[A-Z](?:\s+[0-9]){1,}\b",
        lambda match: re.sub(r"\s+", "", match.group()),
        text,
        flags=re.I,
    )


def _justifications():
    path = Path(__file__).with_name("t1353_locator_label_justifications.yaml")
    entries = yaml.safe_load(path.read_text()) or []
    assert all(entry.get("reason") and entry.get("checked_by_render") for entry in entries)
    return {
        (entry["extract"], tuple(entry["locator_path"]), entry["page"], entry["label"]): entry
        for entry in entries
    }


@lru_cache(maxsize=None)
def _page_text(pdf, page_number):
    result = subprocess.run(
        ["pdftotext", "-f", str(page_number), "-l", str(page_number), "-layout", pdf, "-"],
        check=True, capture_output=True, text=True, timeout=30,
    )
    return result.stdout


@lru_cache(maxsize=None)
def _mineru_page_text(source_id, corpus_source_id, page_index):
    for sid in dict.fromkeys((source_id, corpus_source_id)):
        if not sid:
            continue
        candidates = (
            MINERU_TEXT_ROOT / sid / "mineru" / f"{sid}_content_list.json",
            MINERU_TEXT_ROOT / sid / "mineru/source/vlm/source_content_list.json",
            *(root / sid / "ocr-mineru" / name for root in CORPUS_ROOTS
              for name in (f"{sid}_content_list.json", "source_content_list.json")),
        )
        for path in candidates:
            if not path.is_file():
                continue
            content = json.loads(path.read_text())
            chunks = [chunk for chunk in content if chunk.get("page_idx") == page_index]
            parts = []
            for chunk in chunks:
                for key in (
                    "text", "content", "chart_caption", "chart_footnote",
                    "table_caption", "table_footnote", "table_body",
                ):
                    value = chunk.get(key)
                    if isinstance(value, list):
                        parts.extend(str(item) for item in value)
                    elif isinstance(value, str):
                        parts.append(value)
            return "\n".join(parts)
    return ""


def _has_page_text(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    substantive = [
        line for line in lines
        if not re.fullmatch(r"\d{4}M&PS\.\.\..*", line)
        and not re.fullmatch(r"\d+", line)
    ]
    return bool(substantive)


def _numeric_values(value, parent_key="", published_only=False):
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if not published_only and re.search(r"(^t(?:_|$)|temperature|composition|mole_fraction|^x_)", key_text, re.I):
                continue
            yield from _numeric_values(child, key_text, published_only)
    elif isinstance(value, list):
        for child in value:
            yield from _numeric_values(child, parent_key, published_only)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if not published_only or re.search(r"as_(?:published|printed)$", parent_key):
            yield str(value)
    elif isinstance(value, str) and re.search(r"as_(?:published|printed)$", parent_key):
        measured_text = re.split(r"±|\+/-", value, maxsplit=1)[0]
        match = re.match(r"\s*\(?\s*([−–—﹣－-]?\s*(?:\d+(?:\.\d*)?|\.\d+))", measured_text)
        if match:
            yield re.sub(r"\s+", "", match.group(1))


def _digitized(value):
    if isinstance(value, dict):
        return any(
            _digitized(child) if key != "method_class" else bool(re.search(r"digitiz", str(child), re.I))
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_digitized(child) for child in value)
    return False


def _has_measured_values(values):
    return values.get("quantity") != "apparatus_and_pressure_regime"


def _observation_values(document, locator_path):
    if any(part in {"point_conditions", "standard_state", "composition_role", "uncertainty"} for part in locator_path):
        return
    if locator_path[:1] == ("fidelity_samples",):
        bound = document
        for part in locator_path[:-1]:
            bound = bound[int(part)] if isinstance(bound, list) else bound[part]
        if isinstance(bound, dict) and "value" in bound:
            value_path = str(bound.get("path", bound.get("field", "")))
            note = str(bound.get("note", ""))
            if (re.search(r"as_(?:published|printed)$|value_as_published$", value_path)
                    or re.search(r"\bprinted\b", note, re.I)) and not re.search(r"digitiz", note, re.I):
                field_context = value_path if re.search(r"as_(?:published|printed)$|value_as_published$", value_path) else "value_as_published"
                yield from _numeric_values(bound["value"], parent_key=field_context)
        return
    for index, part in enumerate(locator_path[:-1]):
        if part not in {"observations", "context"} or index + 1 >= len(locator_path) - 1:
            continue
        observation_path = locator_path[:index + 2]
        observation = document
        for component in observation_path:
            observation = observation[int(component)] if isinstance(observation, list) else observation[component]
        if isinstance(observation, dict) and "values" in observation:
            values = observation["values"]
            if not _has_measured_values(values) or _digitized(values):
                return
            yield from _numeric_values(values, published_only=True)
        return


def _generic_observation_values(document, locator_path):
    if locator_path[:1] == ("fidelity_samples",):
        bound = document
        for part in locator_path[:-1]:
            bound = bound[int(part)] if isinstance(bound, list) else bound[part]
        if not isinstance(bound, dict) or "value" not in bound:
            return ()
        value_path = str(bound.get("path", bound.get("field", "")))
        note = str(bound.get("note", ""))
        if re.search(r"digitiz", note, re.I):
            return ()
        if re.search(r"as_(?:published|printed)$|value_as_published$", value_path):
            field_context = value_path
        elif re.search(r"\bprinted\b", note, re.I):
            field_context = "value_as_published"
        else:
            field_context = value_path.rsplit(".", 1)[-1]
        return tuple(_numeric_values(bound["value"], parent_key=field_context))
    for index, part in enumerate(locator_path[:-1]):
        if part not in {"observations", "context"} or index + 1 >= len(locator_path) - 1:
            continue
        observation = document
        for component in locator_path[:index + 2]:
            observation = observation[int(component)] if isinstance(observation, list) else observation[component]
        values = observation.get("values", {}) if isinstance(observation, dict) else {}
        if not _has_measured_values(values):
            return ()
        return tuple(_numeric_values(values))
    return ()


def _skip_class(document, locator_path, locator):
    if any(part in {"point_conditions", "standard_state", "composition_role", "uncertainty", "composition"} for part in locator_path):
        return "categorical" if "composition_role" in locator_path or "composition" in locator_path else "derived"
    if "figure" in locator:
        return "digitized"
    if locator_path[:1] == ("fidelity_samples",):
        bound = document
        for part in locator_path[:-1]:
            bound = bound[int(part)] if isinstance(bound, list) else bound[part]
        if isinstance(bound, dict) and re.search(r"digitiz", str(bound.get("note", "")), re.I):
            return "digitized"
    else:
        for index, part in enumerate(locator_path[:-1]):
            if part not in {"observations", "context"} or index + 1 >= len(locator_path) - 1:
                continue
            observation = document
            for component in locator_path[:index + 2]:
                observation = observation[int(component)] if isinstance(observation, list) else observation[component]
            values = observation.get("values", {}) if isinstance(observation, dict) else {}
            if _digitized(values):
                return "digitized"
            if (re.search(r"composition_role|species|formula|speciation", ".".join(locator_path), re.I)
                    or not tuple(_numeric_values(values))):
                return "categorical"
            if re.search(r"derived|secondary_compilation|arithmetic|calculated|transformed", str(values), re.I):
                return "derived"
            return "categorical"
    if re.search(r"composition|species|formula|speciation", ".".join(locator_path), re.I):
        return "categorical"
    return "derived"


def _number_pattern(value):
    original = unicodedata.normalize("NFKC", str(value)).translate(
        str.maketrans({"−": "-", "–": "-", "—": "-", "﹣": "-", "－": "-"})
    )
    try:
        number = Decimal(original)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    text = format(number, "f")
    negative = original.strip().startswith("-")
    sign = r"[−–—﹣－-]\s*" if negative else ""
    digits = text.lstrip("-")
    if "." in digits:
        integer, fraction = digits.split(".", 1)
        fraction = fraction.rstrip("0")
        if fraction:
            digits = integer + "." + fraction
            trailing_zeros = r"0*"
        else:
            digits = integer
            trailing_zeros = r"(?:\.0+)?"
    else:
        trailing_zeros = r"(?:\.0+)?" if "." in original else ""
    body = r"\s*".join(re.escape(char) for char in digits)
    if digits.startswith("0."):
        body = "0?" + body[1:]
    prefix = r"(?<![\d.])" if negative else r"(?<![\d.−–—﹣－-])"
    return re.compile(rf"{prefix}{sign}{body}{trailing_zeros}(?![\d.])")


def _assert_observation_value(page_text, observation_values):
    observation_values = tuple(observation_values)
    if observation_values and not any(
        pattern is not None and pattern.search(page_text)
        for pattern in (_number_pattern(value) for value in observation_values)
    ):
        raise AssertionError("no printed observation value is present on cited page")


def _assert_label_and_data(locator, locator_path, page_text, caption_page_text=None, observation_values=()):
    label_pattern = _label_pattern(locator)
    caption_page = locator.get("table_caption_page")
    if locator.get("table_continued") is True:
        if type(locator.get("page")) is not int or locator["page"] < 2:
            raise AssertionError("continued table requires a physical data page after page 1")
        caption_page = locator["page"] - 1
    if caption_page is not None:
        if type(caption_page) is not int or not isinstance(locator.get("page"), int):
            raise AssertionError("table caption and data pages must be physical page integers")
        if caption_page >= locator["page"]:
            raise AssertionError("table caption page must precede the data page")
        match = re.search(r"data cell:\s*([^\n]+?)\s*\.?$", locator.get("note", ""), re.I)
        if match:
            record = match.group(1)
        elif "species" in locator_path:
            record = locator_path[locator_path.index("species") + 1]
        elif locator.get("record"):
            record = locator["record"]
        else:
            raise AssertionError("continued table locator requires a data-cell record")
        parts = str(record).split()
        record_pattern = re.compile(
            r"\s+".join(
                r"\s*".join(re.escape(char) for char in part) if part.isdigit() else re.escape(part)
                for part in parts
            )
        )
        if not record_pattern.search(page_text):
            raise AssertionError("continued table data-cell record is absent from cited page")
        if caption_page_text is None or not label_pattern.search(caption_page_text):
            raise AssertionError("table caption label is absent from caption page")
        _assert_observation_value(page_text, observation_values)
        return
    if not label_pattern.search(page_text):
        raise AssertionError("table/figure label is absent from cited page")
    _assert_observation_value(page_text, observation_values)


def test_changed_table_and_figure_locators_name_their_physical_pdf_page(capsys):
    checked = skipped = justified = 0
    pdftotext_matched = mineru_matched = 0
    failures = []
    justifications = _justifications()
    used_justifications = set()
    value_skips = []
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
            if prior is None or not any(
                prior.get(key) != locator.get(key)
                for key in ("page", "published_page", "pdf_page_index", "table_caption_page", "table_continued")
            ):
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
            caption_page = locator.get("table_caption_page")
            if locator.get("table_continued") is True:
                caption_page = page_number - 1
            caption_text = _page_text(str(pdf), caption_page) if caption_page is not None else None
            printed_values = tuple(_observation_values(current, locator_path))
            try:
                _assert_label_and_data(
                    locator,
                    locator_path,
                    _normalize_label_text(text),
                    _normalize_label_text(caption_text) if caption_text is not None else None,
                    printed_values,
                )
            except AssertionError as exc:
                if str(exc) == "no printed observation value is present on cited page":
                    mineru_text = _normalize_label_text(_mineru_page_text(
                        current.get("source_id"),
                        current.get("source", {}).get("corpus_source_id"),
                        page_number - 1,
                    ))
                    try:
                        _assert_observation_value(mineru_text, printed_values)
                    except AssertionError:
                        pass
                    else:
                        checked += 1
                        mineru_matched += 1
                        continue
                    value_class = _skip_class(current, locator_path, locator)
                    kind, value_label = next((key, locator[key]) for key in ("table", "figure") if key in locator)
                    value_skips.append({
                        "extract": relative,
                        "locator": ".".join(locator_path) + f" [{kind} {value_label}, page {page_number}]",
                        "printed_string": "; ".join(printed_values),
                        "class": value_class,
                        "verdict": "label-present; value is not literal page text for this typed locator",
                    })
                    skipped += 1
                    continue
                key = (relative, locator_path, page_number, label)
                justification = justifications.get(key)
                note_cell = re.search(r"data cell:\s*([^\n]+?)\s*\.?$", locator.get("note", ""), re.I)
                label_failure = str(exc) in {
                    "table caption label is absent from caption page",
                    "table/figure label is absent from cited page",
                }
                scanned_cell_failure = (
                    str(exc) == "continued table data-cell record is absent from cited page"
                    and justification is not None
                    and note_cell is not None
                    and justification.get("data_cell") == note_cell.group(1)
                )
                if justification is not None and (label_failure or scanned_cell_failure):
                    used_justifications.add(key)
                    skipped += 1
                    justified += 1
                    continue
                failures.append(f"{relative} {locator_path}: {label} on PDF page {page_number}: {exc}")
                continue
            checked += 1
            if printed_values:
                pdftotext_matched += 1
            if not printed_values:
                generic_values = _generic_observation_values(current, locator_path)
                if generic_values and any(
                    pattern is not None and pattern.search(_normalize_label_text(text))
                    for pattern in (_number_pattern(value) for value in generic_values)
                ):
                    pdftotext_matched += 1
                    continue
                mineru_text = _normalize_label_text(_mineru_page_text(
                    current.get("source_id"),
                    current.get("source", {}).get("corpus_source_id"),
                    page_number - 1,
                ))
                if generic_values and any(
                    pattern is not None and pattern.search(mineru_text)
                    for pattern in (_number_pattern(value) for value in generic_values)
                ):
                    mineru_matched += 1
                    continue
                kind, label = next((key, locator[key]) for key in ("table", "figure") if key in locator)
                value_skips.append({
                    "extract": relative,
                    "locator": ".".join(locator_path) + f" [{kind} {label}, page {page_number}]",
                    "printed_string": "",
                    "class": _skip_class(current, locator_path, locator),
                    "verdict": "label-present; no literal printed numeric value to assert",
                })
                skipped += 1
    print(
        f"t1353 PDF locator labels: {checked} passed, {skipped} skipped "
        f"({justified} justified by 600-dpi render), {len(failures)} failed"
    )
    print(f"printed values: {pdftotext_matched} pdftotext-matched, {mineru_matched} MinerU-matched")
    assert checked > 0, "no locator labels were checked"
    assert failures == [], "PDF locator label failures:\n" + "\n".join(failures)
    assert used_justifications == set(justifications), "stale or unmatched rendered locator justification"
    csv_path = Path(__file__).with_name("t1353_locator_pdf_value_skips.csv")
    with csv_path.open(newline="") as stream:
        recorded_skips = list(csv.DictReader(stream))
    assert len(value_skips) == len(recorded_skips), "typed locator value skip count differs from committed CSV"
    value_render_justified = 0
    recorded_fields = ("extract", "locator", "printed_string", "class", "verdict")
    for expected, recorded in zip(value_skips, recorded_skips):
        if recorded["class"] == "render-confirmed":
            crop_path = Path(recorded["crop_path"])
            assert not crop_path.is_absolute(), "render-confirmed crop path must be repository-relative"
            assert (ROOT / crop_path).is_file(), "render-confirmed value skip crop does not resolve"
            expected["class"] = "render-confirmed"
            expected["verdict"] = "render-confirmed; no text layer"
            value_render_justified += 1
        assert {field: expected[field] for field in recorded_fields} == {
            field: recorded[field] for field in recorded_fields
        }, "typed locator value skip differs from committed CSV"
    assert all(
        not Path(row["crop_path"]).is_absolute()
        and (ROOT / row["crop_path"]).is_file()
        for row in recorded_skips if row["class"] == "render-confirmed"
    )
    print(f"render-justified values: {value_render_justified}")
    print(f"typed value skips: {sum(row['class'] == 'digitized' for row in value_skips)} digitized, "
          f"{sum(row['class'] == 'categorical' for row in value_skips)} categorical, "
          f"{sum(row['class'] == 'derived' for row in value_skips)} derived")
    captured = capsys.readouterr()
    print(captured.out, end="")


def test_reiss_apparatus_values_are_not_measured_observation_values():
    path = ROOT / "data/literature/extracts/reiss-2019-thermal-extraction-nulht2m.yaml"
    document = _yaml(path.read_text())
    locator_path = ("species", "NU-LHT-2M", "observations", "2", "locator")
    assert tuple(_observation_values(document, locator_path)) == ()
    assert _generic_observation_values(document, locator_path) == ()


def test_printed_string_checks_central_measured_value_with_field_context():
    import pytest

    document = {
        "fidelity_samples": [
            {
                "path": "species.Ca.observations[row].values.P_Ca_as_printed",
                "value": "2.2 ± 0.2",
                "locator": {"table": "Ia", "page": 2, "pdf_page_index": 1},
            }
        ]
    }
    locator_path = ("fidelity_samples", "0", "locator")
    measured_value = tuple(_observation_values(document, locator_path))

    assert measured_value == ("2.2",)
    assert tuple(_numeric_values("NaCl(l) + K2O(slag)", parent_key="reaction_as_printed", published_only=True)) == ()
    assert tuple(_numeric_values("P_KCl / P_K(no NaCl) ~ 10^4", parent_key="expression_as_printed", published_only=True)) == ()
    _assert_observation_value("P_Ca 2.2 ± 0.2", measured_value)
    with pytest.raises(AssertionError, match="no printed observation value"):
        _assert_observation_value("P_Ca 0.2", measured_value)
    with pytest.raises(AssertionError, match="no printed observation value"):
        _assert_observation_value("P_Ca -2.2", measured_value)


def test_tsuzuki_printed_coefficient_is_checked_as_its_measured_field():
    document = _yaml((ROOT / "data/literature/extracts/ts1985.yaml").read_text())
    observation = document["species"]["Na2O"]["observations"][0]
    row = observation["values"]["rows"][0]
    measured_value = tuple(
        _numeric_values(row["A_as_printed"], parent_key="A_as_printed", published_only=True)
    )
    assert measured_value == ("-1.46",)
    pdf = _pdf_path(document.get("source_id"), document.get("source", {}).get("corpus_source_id"))
    assert pdf is not None
    assert _label_pattern(observation["locator"]).search(
        _normalize_label_text(_page_text(str(pdf), 7))
    )


def test_continued_table_checks_caption_page_and_data_page():
    locator = {
        "table": "877",
        "page": 4,
        "table_continued": True,
        "record": "3300.000 64.806",
        "note": "Table 877 continues; data cell shown in record.",
    }
    _assert_label_and_data(
        locator,
        (),
        "T (K)  F°(T)\n3300.000 64.806 346.974",
        "Class accuracy\nTable 877",
    )


def test_continued_table_requires_label_on_preceding_physical_page():
    import pytest

    locator = {
        "table": "877",
        "page": 4,
        "table_continued": True,
        "record": "3300.000 64.806",
    }
    with pytest.raises(AssertionError, match="caption label is absent"):
        _assert_label_and_data(
            locator,
            (),
            "Table 877\n3300.000 64.806 346.974",
            "Class accuracy",
        )


def test_zigo_table_1_prose_mention_does_not_satisfy_data_locator():
    relative = "data/literature/extracts/zigo-1987-gehlenite-fusion.yaml"
    document = _yaml((ROOT / relative).read_text())
    observation = document["species"]["gehlenite"]["observations"][0]
    locator = observation["locator"]
    assert locator["page"] == 4
    assert locator["pdf_page_index"] == 3
    assert locator["published_page"] == 174
    pdf = _pdf_path(document.get("source_id"), document.get("source", {}).get("corpus_source_id"))
    assert pdf is not None
    page_five = _normalize_label_text(_page_text(str(pdf), 5))
    assert _label_pattern(locator).search(page_five), "mutation fixture must include the prose label mention"
    try:
        _assert_label_and_data(
            {**locator, "page": 5, "pdf_page_index": 4},
            ("species", "gehlenite", "observations", "0", "locator"),
            page_five,
            observation_values=_numeric_values(observation["values"]),
        )
    except AssertionError as exc:
        assert str(exc) == "no printed observation value is present on cited page"
    else:
        raise AssertionError("Table 1 page-5 prose mention incorrectly satisfied the data locator")


def test_zigo_equation_and_prose_locators_bind_to_their_physical_pages():
    document = _yaml((ROOT / "data/literature/extracts/zigo-1987-gehlenite-fusion.yaml").read_text())
    observations = {
        row["observation_id"]: row
        for row in document["species"]["gehlenite"]["observations"]
    }
    pins = (
        ("zigo_1987_t4_melt_relative_enthalpy_fit", 8, 7, 178, "6"),
        ("zigo_1987_fusion_enthalpy_at_1863K", 9, 8, 179, "7"),
        ("zigo_1987_fusion_entropy_at_1863K", 9, 8, 179, "8"),
        ("zigo_1987_fusion_temperature_sensitivity", 10, 9, 180, None),
    )
    for observation_id, page, index, folio, equation in pins:
        locator = observations[observation_id]["locator"]
        assert (locator["page"], locator["pdf_page_index"], locator["published_page"]) == (
            page, index, folio
        )
        if equation is not None:
            assert locator["equation"] == equation
    pdf = _pdf_path(document.get("source_id"), document.get("source", {}).get("corpus_source_id"))
    assert pdf is not None
    page7 = _normalize_label_text(_page_text(str(pdf), 7))
    page8 = _normalize_label_text(_page_text(str(pdf), 8))
    page9 = _normalize_label_text(_page_text(str(pdf), 9))
    page10 = _normalize_label_text(_page_text(str(pdf), 10))
    assert re.search(r"\(\s*6\s*\)", page8) and "2.9" in page8
    assert not re.search(r"\(\s*6\s*\)", page7)
    assert re.search(r"\(\s*7\s*\)", page9) and "172.1" in page9
    assert re.search(r"\(\s*[8S]\s*\)", page9) and re.search(r"9\s*2\s*4", page9)
    assert "1857" in page10 and "1866" in page10


def test_figure_caption_regex_handles_spacing_and_japanese_suffix():
    pattern = _label_pattern({"figure": "8"})
    assert pattern.search("Fig .   8. Caption")
    assert pattern.search("Results in Fig.8に shown below")


def test_numeric_pattern_normalizes_unicode_minus():
    assert _number_pattern("−5.703").search("value - 5.703")


def test_slag_figure_9_uses_caption_page_not_page_7_prose():
    relative = "data/literature/extracts/slag-002-banya-hino-nagasaka-1993.yaml"
    document = _yaml((ROOT / relative).read_text())
    matches = [
        (path, locator)
        for path, locator in _walk_locators(document)
        if locator.get("figure") == "9" and locator.get("page") is not None
    ]
    assert len(matches) == 1
    locator_path, locator = matches[0]
    assert locator["page"] == 6
    assert locator["pdf_page_index"] == 5
    assert locator["published_page"] == 17
    pdf = _pdf_path(document.get("source_id"), document.get("source", {}).get("corpus_source_id"))
    assert pdf is not None
    _assert_label_and_data(locator, locator_path, _normalize_label_text(_page_text(str(pdf), 6)))
