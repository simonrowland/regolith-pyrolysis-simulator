from __future__ import annotations

from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/nasa-tm-2024-simulant-guide.yaml"

ATTRIBUTIONS = {
    "nasa_tm_2024_high_temperature_vacuum_guidance": (
        "Figure 24 caption: Petkov and Voecks (2023)."
    ),
    "nasa_tm_2024_table25_crystalline_silica": (
        "Table 25 footnotes: § DCM Science Laboratories; ‡ Herndon Solutions Group; "
        "¤ Innovative Health Applications - Industrial Hygiene Office; RJ Lee Group."
    ),
}


def _assert_quoted_attributions(document: dict) -> None:
    observations = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }
    for observation_id, attribution in ATTRIBUTIONS.items():
        values = observations[observation_id]["values"]
        assert values["method_class"] == "quoted_literature"
        assert values["evidence_class"] == "quoted_attributed"
        assert values.get("attribution") == attribution


def test_sc456_quoted_rows_name_the_printed_sources() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_quoted_attributions(document)

    target = next(
        row
        for species in document["species"].values()
        for row in species.get("observations", [])
        if row["observation_id"] == "nasa_tm_2024_high_temperature_vacuum_guidance"
    )
    del target["values"]["attribution"]
    with pytest.raises(AssertionError):
        _assert_quoted_attributions(document)
