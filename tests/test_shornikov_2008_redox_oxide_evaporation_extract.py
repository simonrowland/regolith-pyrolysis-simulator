"""Pin prose locators to the source page that contains each statement."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from tests.helpers.corpus_paths import regolith_corpus_root
from tests.helpers.pdf_text import pdftotext_page_text

ROOT = Path(__file__).resolve().parents[1]
SID = "shornikov-2008-redox-oxide-evaporation"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"
STATEMENT_PINS: dict[str, tuple[str, ...]] = {
    "shornikov_2008_prose_sio2_minimum": (
        "log p° ( O ) = –5.75",
        "minimum of total vapor pressure over SiO2",
        "Fig. 2a, curve V",
    ),
    "shornikov_2008_prose_alumina_minimum": (
        "minimum of total vapor pressure",
        "over Al2O3 at log p° ( O ) = –8.0",
        "Fig. 2b, curve IV",
    ),
}


def _compact(text: str) -> str:
    dehyphenated = re.sub(r"-\s*\n\s*", "", text)
    return re.sub(r"[^a-z0-9]+", "", dehyphenated.casefold())


def _contains_all_pins(text: str, pins: tuple[str, ...]) -> bool:
    normalized = _compact(text.replace("Äl2é3", "Al2O3"))
    return all(_compact(pin) in normalized for pin in pins)


@pytest.mark.parametrize(("observation_id", "source_pins"), STATEMENT_PINS.items())
def test_minimum_statement_matches_its_cited_source_page(
    observation_id: str, source_pins: tuple[str, ...]
) -> None:
    corpus = regolith_corpus_root()
    pdf = corpus / f"raw/{SID}/{SID}.pdf"
    if not pdf.is_file():
        pytest.skip("Shornikov 2008 source PDF is absent")

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = {
        observation["observation_id"]: observation
        for species in document["species"].values()
        for collection in ("observations", "context")
        for observation in species.get(collection, [])
    }
    observation = observations[observation_id]
    locator = observation["locator"]
    statement = observation["values"]["statement"]
    assert _contains_all_pins(statement, source_pins)

    source_text = pdftotext_page_text(pdf, locator["page"])
    assert _contains_all_pins(source_text, source_pins)

    assert locator["page"] == 3
    assert locator["pdf_page_index"] == 2
    assert locator["published_page"] == 726

    next_page_text = pdftotext_page_text(pdf, locator["page"] + 1)
    assert not _contains_all_pins(next_page_text, source_pins)
