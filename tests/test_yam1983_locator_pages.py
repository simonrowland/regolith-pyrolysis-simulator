"""Source-text pin for the Yamamoto (1983) English-abstract locator."""

from pathlib import Path

import yaml

from tests.literature_extract_test_helpers import source_page_text


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/yam1983.yaml"


def test_na2o_activity_range_locator_binds_to_english_abstract() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observation = next(
        row
        for row in document["species"]["Na2O"]["observations"]
        if row["observation_id"] == "yam1983_na2o_prose_activity_range"
    )
    locator = observation["locator"]
    assert (locator["page"], locator["pdf_page_index"], locator["published_page"]) == (
        1,
        0,
        736,
    )

    page1 = source_page_text(document["source_id"], locator["page"])
    assert "736" in page1
    assert "extremely large negative deviation" in page1
    assert "10−10 to 10−3" in page1
