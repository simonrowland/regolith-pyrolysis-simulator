from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/navrotsky-koryttseva-2023-oxide-acid-base-calorimetry.yaml"


def test_calorimetry_tables_are_preserved_with_schema_gap_and_figures_deferred():
    extract = yaml.safe_load(EXTRACT.read_text())
    context = extract["species"]["SiO2"]["context"]
    tables = [item for item in context if item["type"] == "table_context"]
    assert len(tables) == 2
    assert "−193.68" in tables[0]["values"]["printed_table_text"]
    assert "−87.94" in tables[1]["values"]["printed_table_text"]
    assert all("No enthalpy-of-solution quantity token" in item["values"]["schema_gap"] for item in tables)
    figures = [item for item in context if item["type"] == "figure_context"]
    assert len(figures) == 8
    assert all(item["values"]["digitisation"] == "deferred to a figure pass" for item in figures)
