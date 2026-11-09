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
STATEMENT_PINS = {
    "shornikov_2008_prose_sio2_minimum": (
        "log p° ( O ) = –5.75 corresponds to the minimum of total vapor pressure "
        "over SiO2 (Fig. 2a, curve V)"
    ),
    "shornikov_2008_prose_alumina_minimum": (
        "minimum of total vapor pressure over Al2O3 at log p° ( O ) = –8.0 "
        "(Fig. 2b, curve IV)"
    ),
}


def _compact(text: str) -> str:
    dehyphenated = re.sub(r"-\s*\n\s*", "", text)
    return re.sub(r"[^a-z0-9]+", "", dehyphenated.casefold())


@pytest.mark.parametrize(("observation_id", "source_phrase"), STATEMENT_PINS.items())
def test_minimum_statement_matches_its_cited_source_page(
    observation_id: str, source_phrase: str
) -> None:
    corpus = regolith_corpus_root()
    pdf = corpus / f"raw/{SID}/{SID}.pdf"
    if not pdf.is_file():
        pytest.skip("Shornikov 2008 source PDF is absent")

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = {
        observation["observation_id"]: observation
        for species in document["species"].values()
        for observation in species.get("observations", [])
    }
    observation = observations[observation_id]
    locator = observation["locator"]
    assert locator["page"] == 3
    assert locator["pdf_page_index"] == 2
    assert locator["published_page"] == 726

    statement = observation["values"]["statement"]
    assert _compact(source_phrase) in _compact(statement)
    source_text = pdftotext_page_text(pdf, locator["page"])
    source_text = source_text.replace("Äl2é3", "Al2O3")
    assert _compact(source_phrase) in _compact(source_text)
