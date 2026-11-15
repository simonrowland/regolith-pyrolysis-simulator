"""Audit Mendybaev 2017 extract locators against their cited PDF pages."""

from __future__ import annotations

import csv
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/mendybaev-2017-fun-cai-lab-evaporation.yaml"
PDF = Path(
    "/Users/simonrowland/Repos/regolith-corpus/raw/"
    "mendybaev-2017-fun-cai-lab-evaporation/"
    "mendybaev-2017-fun-cai-lab-evaporation.pdf"
)
PDF_SHA256 = "a2e99863bc07401d0d87607430ef78fbb61c7a6840405dc58467255a9cf0185b"
RECEIPT = (
    ROOT
    / "data/literature/extracts/audits/"
    "mendybaev-2017-fun-cai-lab-evaporation.locator-page-audit.csv"
)


def _scalar_text(value: Any) -> str | None:
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _table_value(locator: dict[str, Any], contexts: list[dict[str, Any]]) -> str | None:
    column = str(locator.get("column", ""))
    lowered = column.casefold()
    for context in reversed(contexts):
        if "time" in lowered or "duration" in lowered:
            state = context.get("state")
            if isinstance(state, dict):
                value = _scalar_text(state.get("value"))
                if value is not None:
                    return value
        direct = _scalar_text(context.get(column))
        if direct is not None:
            return direct
        if "sigma" in lowered or "σ" in lowered:
            sigma = _scalar_text(context.get("sigma"))
            if sigma is not None:
                return sigma
        else:
            candidates = [
                (str(key), _scalar_text(value))
                for key, value in context.items()
                if str(key).casefold().startswith(lowered)
            ]
            candidates = [
                (key, value)
                for key, value in candidates
                if value is not None and "sigma" not in key.casefold() and "σ" not in key
            ]
            if candidates:
                return candidates[0][1]
    return None


def _phrase_for_locator(
    locator: dict[str, Any], contexts: list[dict[str, Any]]
) -> str:
    if "row" in locator and "column" in locator:
        row = str(locator["row"])
        value = _table_value(locator, contexts)
        if value is not None:
            return value
        return row

    for context in reversed(contexts):
        value = _scalar_text(context.get("value"))
        if value:
            return value

    for key in ("paragraph", "quote", "quotation", "phrase", "quoted_text"):
        phrase = _scalar_text(locator.get(key))
        if phrase:
            return phrase

    for context in reversed(contexts):
        for key in ("quote", "quotation", "phrase", "quoted_text", "as_printed"):
            phrase = _scalar_text(context.get(key))
            if phrase:
                return phrase

    if "table" in locator:
        return f"Table {locator['table']}"
    if "figure" in locator:
        return f"Figure {locator['figure']}"
    return ""


def collect_locator_records(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every locator path, with its nearest extract phrase or pinned value."""
    records: list[dict[str, Any]] = []

    def walk(value: Any, path: tuple[str, ...], contexts: list[dict[str, Any]]) -> None:
        if isinstance(value, dict):
            if "pdf_page_index" in value:
                records.append(
                    {
                        "path": ".".join(path),
                        "pdf_page_index": value["pdf_page_index"],
                        "physical_page": value["pdf_page_index"] + 1,
                        "phrase": _phrase_for_locator(value, contexts),
                    }
                )
            next_contexts = contexts + [value]
            for key, child in value.items():
                walk(child, path + (str(key),), next_contexts)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, path + (str(index),), contexts)

    walk(document, (), [])
    return records


def _normalize(text: str) -> str:
    without_gutters = re.sub(r"(?m)^\s*\d{3,4}\s+", "", text)
    return " ".join(without_gutters.split())


def _page_text(page: int) -> str:
    return subprocess.run(
        [
            "pdftotext",
            "-layout",
            "-f",
            str(page),
            "-l",
            str(page),
            str(PDF),
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def audit() -> int:
    actual_sha = hashlib.sha256(PDF.read_bytes()).hexdigest()
    if actual_sha != PDF_SHA256:
        raise SystemExit(f"unexpected corpus PDF sha256: {actual_sha}")

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    records = collect_locator_records(document)
    page_text: dict[int, str] = {}
    failures = 0
    checked = 0

    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    with RECEIPT.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=("path", "pdf_page_index", "physical_page", "phrase", "result"),
        )
        writer.writeheader()
        for record in records:
            phrase = record["phrase"]
            if phrase:
                checked += 1
                physical_page = record["physical_page"]
                if physical_page not in page_text:
                    page_text[physical_page] = _page_text(physical_page)
                result = (
                    "PASS"
                    if _normalize(phrase) in _normalize(page_text[physical_page])
                    else "FAIL"
                )
            else:
                result = "SKIP_NO_PHRASE_OR_PIN"
            failures += result == "FAIL"
            row = {**record, "result": result}
            writer.writerow(row)
            print(
                f"{record['pdf_page_index']} -> {record['physical_page']} -> "
                f"{phrase or '<no phrase or pin>'} -> {result}"
            )

    print(
        f"visited {len(records)} locators; checked {checked}; "
        f"{failures} FAIL; receipt {RECEIPT.relative_to(ROOT)}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(audit())
