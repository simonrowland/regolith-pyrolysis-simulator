from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/saridikmen-2009-phosphide-sulphide.yaml"
SID = "saridikmen-2009-phosphide-sulphide"
CORPUS = Path(os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus"))


@pytest.mark.skipif(not (CORPUS / "raw" / SID / f"{SID}.pdf").is_file(), reason="corpus source is absent")
def test_context_tables_and_deferred_capacity_figures(tmp_path: Path) -> None:
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    species = source["species"]["MnO"]
    assert species["observations"] == []
    table1, table2 = species["context"][:2]
    assert table1["values"]["oxygen_partial_pressure_as_printed"] == "around 1.22 × 10^-16 atm under CO"
    assert table1["values"]["oxygen_partial_pressure_determined_as_printed"] == "1.21 × 10^-16 atm at 1773 K"
    assert table1["values"]["carbon_monoxide_partial_pressure_as_printed"] == "pCO = 0.83 atm"
    rows1 = [line for line in table1["values"]["table_values_as_printed"].splitlines() if line.strip().split()[:1] and line.strip().split()[0].isdigit()]
    rows2 = [line for line in table2["values"]["table_values_as_printed"].splitlines() if line.strip().split()[:1] and line.strip().split()[0].isdigit()]
    assert len(rows1) == len(rows2) == 16
    assert any("1" in line and "45.92" in line and "-14.624" in line and "-8.112" in line for line in rows1)
    assert any("16" in line and "41.67" in line and "-14.222" in line and "-7.903" in line for line in rows1)
    assert any("1" in line and "66.1" in line and "0.016" in line and "0.038" in line for line in rows2)
    assert "phosphide or sulphide capacity" in table1["values"]["disposition_reason"]

    def assert_capacity_cells(text: str) -> None:
        assert "-14.624" in text and "-8.112" in text and "-13.437" in text and "-7.554" in text

    text = table1["values"]["table_values_as_printed"]
    assert_capacity_cells(text)
    mutated = deepcopy(text).replace("-14.624", "-14.625", 1)
    with pytest.raises(AssertionError):
        assert_capacity_cells(mutated)

    figures = [record for record in species["context"] if record.get("type") == "figure_record"]
    assert {record["locator"]["figure"] for record in figures} == {str(n) for n in range(1, 11)}
    assert all(record["values"]["digitisation"] == "deferred to a figure pass" for record in figures)
    assert all(record["locator"]["page"] == record["locator"]["pdf_page_index"] + 1 for record in figures)

    from tests.battery.test_migrate import _migrate_real_extract

    migrated = _migrate_real_extract(tmp_path, EXTRACT.name)
    assert not [o for o in migrated.observations.values() if o.source_id == SID]
