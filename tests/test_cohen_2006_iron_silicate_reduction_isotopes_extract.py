from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/cohen-2006-iron-silicate-reduction-isotopes.yaml"


def test_tables_and_figures_are_preserved_without_digitized_points():
    extract = yaml.safe_load(EXTRACT.read_text())
    context = extract["species"]["Fe"]["context"]
    tables = {item["observation_id"]: item for item in context if item["type"] == "table_context"}
    assert len(tables) == 4
    assert "0.13" in tables["cohen_2006_table_2_context"]["values"]["printed_table_text"]
    figures = [item for item in context if item["type"] == "figure_context"]
    assert figures
    assert all(item["values"]["digitisation"] == "deferred to a figure pass" for item in figures)
    assert not extract["species"]["Fe"]["observations"]
