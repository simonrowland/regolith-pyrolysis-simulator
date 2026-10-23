from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/saito-2025-transition-metal-oxide-criteria.yaml"
SID = "saito-2025-transition-metal-oxide-criteria"
CORPUS = Path(os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus"))


@pytest.mark.skipif(not (CORPUS / "raw" / SID / f"{SID}.pdf").is_file(), reason="corpus source is absent")
def test_saito_2025_model_context_and_deferred_figures(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    species = document["species"]
    assert all(not block["observations"] for block in species.values())
    figures = [
        item for block in species.values() for item in block["context"]
        if item.get("type") == "figure_record"
    ]
    assert {item["locator"]["figure"] for item in figures} == {str(n) for n in range(1, 9)}
    assert all(item["values"]["digitisation"] == "deferred to a figure pass" for item in figures)
    assert all(item["locator"]["page"] == item["locator"]["pdf_page_index"] + 1 for item in figures)
    table = species["CrO1.5"]["context"][0]["values"]["rows"]
    assert table[0]["present_study"] == "132000 - 57.7(T/K) J mol-1"
    assert table[2]["present_study"] == "0.308"
    model = species["FeO1.5"]["context"][0]["values"]
    assert model["activity_FeO_at_X_FeO_zero"] == "0.001 (>0)"
    assert model["activity_FeO1.5_at_X_FeO1.5_zero"] == "0.034 (>0)"
    assert species["CrO"]["context"][0]["values"]["quoted_conditions"]["Morita et al. [3] oxygen partial pressure"] == "6.95 × 10^-11 atm"

    from tests.battery.test_migrate import _migrate_real_extract

    migrated = _migrate_real_extract(tmp_path, EXTRACT.name)
    assert not [o for o in migrated.observations.values() if o.source_id == SID]
