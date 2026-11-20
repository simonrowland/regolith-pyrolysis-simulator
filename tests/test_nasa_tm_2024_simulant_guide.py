from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.battery.test_migrate import _migrate_real_extract


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
    source_rows = {
        row["observation_id"]: row
        for species in document["species"].values()
        for category in ("observations", "context")
        for row in species.get(category, [])
    }
    for observation_id, attribution in ATTRIBUTIONS.items():
        values = source_rows[observation_id]["values"]
        assert values["method_class"] == "quoted_literature"
        assert values["evidence_class"] == "quoted_attributed"
        assert values.get("attribution") == attribution


def test_sc456_quoted_rows_name_the_printed_sources() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_quoted_attributions(document)

    target = next(
        row
        for species in document["species"].values()
        for category in ("observations", "context")
        for row in species.get(category, [])
        if row["observation_id"] == "nasa_tm_2024_high_temperature_vacuum_guidance"
    )
    del target["values"]["attribution"]
    with pytest.raises(AssertionError):
        _assert_quoted_attributions(document)


def test_figure24_context_rows_migrate_outside_observations(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    result = _migrate_real_extract(
        tmp_path,
        EXTRACT.name,
        extract_doc=document,
    )

    context_ids = {
        row["observation_id"]
        for rows in result.context_by_work.values()
        for row in rows
    }
    observation_ids = {
        observation.observation_id.rsplit("::", 1)[-1]
        for observation in result.observations.values()
    }
    figure24_ids = {
        "nasa_tm_2024_high_temperature_vacuum_guidance",
        "nasa_tm_2024_figure24_evolved_species",
    }
    assert figure24_ids <= context_ids
    assert not figure24_ids & observation_ids
