from copy import deepcopy
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/colson-nermoe-1998-al-stabilized-cr3-dimer-lpsc.yaml"


def _assert_abstract_pins(document):
    context = document["species"]["Cr"]["context"]
    numeric = next(item for item in context if item["observation_id"].endswith("abstract_numeric_statements"))
    statements = numeric["values"]["statements"]
    assert len(statements) == 4
    assert "0.4 to 0.6 wt%" in statements[0]["claim"]
    assert "3–4 times" in statements[1]["claim"]
    assert "0.21 wt%" in statements[2]["claim"]
    assert "0.1 wt%" in statements[3]["claim"]
    assert numeric["values"]["studied_compositions"][-1] == "0.25Al2O3·1CaO·0.75MgO·2SiO2"
    figures = next(item for item in context if item["observation_id"].endswith("figures_1_2"))
    assert len(figures["values"]["figures"]) == 2
    assert all(item["digitisation"] == "deferred to a figure pass" for item in figures["values"]["figures"])


def test_colson_nermoe_abstract_numbers_and_deferred_figures_are_pinned():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_abstract_pins(document)


def test_colson_nermoe_prose_number_mutation_fails_pin():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = deepcopy(document)
    numeric = next(item for item in mutated["species"]["Cr"]["context"] if item["observation_id"].endswith("abstract_numeric_statements"))
    numeric["values"]["statements"][2]["claim"] = "0.22 wt%"
    with pytest.raises(AssertionError):
        _assert_abstract_pins(mutated)
