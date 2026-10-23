from copy import deepcopy
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/hirashima-1974-cr-redox-phosphate-glass.yaml"


def _assert_table_pins(document):
    context = document["species"]["Cr"]["context"]
    table1 = next(item for item in context if item["observation_id"].endswith("table1_nominal_phosphate_glass_compositions"))
    table2 = next(item for item in context if item["observation_id"].endswith("table2_optical_transition_energies"))
    assert len(table1["values"]["rows"]) == 10
    assert (table1["values"]["rows"][0]["sample"], table1["values"]["rows"][0]["Cr2O3"], table1["values"]["rows"][0]["MgP2O6"]) == ("Mg-1", "2.5", "97.5")
    assert (table1["values"]["rows"][-1]["sample"], table1["values"]["rows"][-1]["Cr2O3"], table1["values"]["rows"][-1]["Ca0.5KP2O6"]) == ("CaK", "2.5", "97.5")
    assert len(table2["values"]["rows"]) == 8
    assert table2["values"]["rows"][0]["Mg_observed"] == "15000"
    assert table2["values"]["rows"][-1]["soda_lime_silica_quoted"] == "15750"
    figures = next(item for item in context if item["observation_id"].endswith("figures_1_to_6"))
    assert len(figures["values"]["figures"]) == 9
    assert all("deferred to a figure pass" == fig["digitisation"] for fig in figures["values"]["figures"])


def test_hirashima_context_tables_and_deferred_figures_are_pinned():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_table_pins(document)
    assert "corpus PDF was read with pdftotext" in document["extraction"]["method"]
    assert document["extraction"]["provenance_path"].endswith("hirashima-1974-cr-redox-phosphate-glass.pdf")


def test_hirashima_table_cell_mutation_fails_render_pin():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = deepcopy(document)
    table1 = next(item for item in mutated["species"]["Cr"]["context"] if item["observation_id"].endswith("table1_nominal_phosphate_glass_compositions"))
    table1["values"]["rows"][0]["Cr2O3"] = "2.6"
    with pytest.raises(AssertionError):
        _assert_table_pins(mutated)
