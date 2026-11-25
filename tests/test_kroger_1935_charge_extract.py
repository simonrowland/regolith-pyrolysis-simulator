"""Source-located starting-charge transcription for Kroger and Fingas (1935)."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/kroger-fingas-1935-alkali-silicate-equilibria.yaml"
)


def test_table_7_printed_charge_recipe_is_linked_to_source_locator() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observation = next(
        row
        for row in document["species"]["CO2"]["observations"]
        if row["observation_id"] == "kroger_fingas_1935_k2si2o5_k2co3_table7"
    )

    assert observation["values"]["composition_as_printed"] == (
        "0.6429 g K2Si2O5 + 0.8292 g K2CO3; molar ratio 1:2"
    )
    assert observation["locator"] == {
        "page": 18,
        "published_page": 18,
        "pdf_page_index": 17,
        "table": "7",
    }
