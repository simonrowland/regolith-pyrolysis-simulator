"""Source-text pins for Halwax et al. (2024) physical-page locators."""

from pathlib import Path

import yaml

from tests.literature_extract_test_helpers import source_page_text


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kems-031-halwax-2024.yaml"


def test_edited_locators_bind_to_their_physical_pdf_pages() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    page_six_locators = (
        document["experiments"][2]["locator"],
        document["experiments"][2]["sample"]["printed_composition"]["locator"],
        document["species"]["Ni"]["observations"][1]["uncertainty"]["locator"],
    )
    for locator in page_six_locators:
        assert (locator["page"], locator["pdf_page_index"], locator["published_page"]) == (
            6,
            5,
            826,
        )

    ionization_locator = document["benches"][0]["ionization"]["energy_eV"]["locator"]
    assert (
        ionization_locator["page"],
        ionization_locator["pdf_page_index"],
        ionization_locator["published_page"],
    ) == (5, 4, 825)

    page6 = source_page_text(document["source_id"], 6)
    assert "826" in page6
    assert "A. Calibration" in page6
    assert "pure Nickel standard" in page6
    assert "Table II" in page6 and "418 ± 8.4" in page6

    page5 = source_page_text(document["source_id"], 5)
    assert "electron energy of 70 eV" in page5
    assert "emission current of 0.2 mA" in page5
    assert "0.2 mA" not in page6
