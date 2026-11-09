"""Source-text pins for Halwax et al. (2024) physical-page locators."""

from pathlib import Path

import yaml

from tests.literature_extract_test_helpers import source_page_text


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kems-031-halwax-2024.yaml"


def test_page_826_locators_bind_to_physical_pdf_page_six() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    locators = (
        document["benches"][0]["ionization"]["energy_eV"]["locator"],
        document["experiments"][1]["locator"],
        document["experiments"][1]["sample"]["printed_composition"]["locator"],
        document["species"]["Ni"]["observations"][1]["values"]["uncertainty"]["locator"],
    )
    for locator in locators:
        assert (locator["page"], locator["pdf_page_index"], locator["published_page"]) == (
            6,
            5,
            826,
        )

    page6 = source_page_text(document["source_id"], 6)
    assert "826" in page6
    assert "70 eV" in page6
    assert "A. Calibration" in page6
    assert "Table II" in page6 and "418 ± 8.4" in page6
