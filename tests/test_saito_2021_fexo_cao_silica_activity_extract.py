from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/saito-2021-fexo-cao-silica-activity.yaml"
SID = "saito-2021-fexo-cao-silica-activity"
CORPUS = Path(os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus"))


@pytest.mark.skipif(not (CORPUS / "raw" / SID / f"{SID}.pdf").is_file(), reason="corpus source is absent")
def test_nonstoichiometric_fexo_context_and_deferred_figures(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    species = document["species"]["FeO"]
    assert species["observations"] == []
    table1, table2 = species["context"][:2]
    assert table1["locator"]["table"] == "1"
    assert "22.0" in table1["values"]["table_values_as_printed"]
    text = table2["values"]["table_values_as_printed"]
    rows = [line for line in text.splitlines() if line.strip().split()[0].count("-") == 1 and line.strip().split()[0].split("-")[0].isdigit()]
    assert len(rows) == 42
    assert any("1-1" in line and "−10.97" in line and "0.727" in line and "0.616" in line for line in rows)
    assert any("9-7" in line and "−10.72" in line and "0.968" in line and "0.820" in line for line in rows)
    assert "pure non-stoichiometric solid FexO" in table2["values"]["standard_states_as_printed"]["a_FexO_s"]
    assert "pure non-stoichiometric liquid FexO" in table2["values"]["standard_states_as_printed"]["a_FexO_l"]

    def assert_key_cells(context_text: str) -> None:
        assert "−10.97" in context_text and "0.727" in context_text and "0.616" in context_text
        assert "−10.72" in context_text and "0.968" in context_text and "0.820" in context_text

    assert_key_cells(text)
    mutated = deepcopy(text).replace("0.727", "0.728", 1)
    with pytest.raises(AssertionError):
        assert_key_cells(mutated)

    figures = [item for item in species["context"] if item.get("type") == "figure_record"]
    assert {item["locator"]["figure"] for item in figures} == {str(n) for n in range(1, 10)}
    assert all(item["values"]["digitisation"] == "deferred to a figure pass" for item in figures)
    assert all(item["locator"]["page"] == item["locator"]["pdf_page_index"] + 1 for item in figures)

    from tests.battery.test_migrate import _migrate_real_extract

    migrated = _migrate_real_extract(tmp_path, EXTRACT.name)
    assert not [o for o in migrated.observations.values() if o.source_id == SID]
