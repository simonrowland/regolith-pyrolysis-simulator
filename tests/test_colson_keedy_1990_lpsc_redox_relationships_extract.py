from copy import deepcopy
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/colson-keedy-1990-lpsc-redox-relationships.yaml"


def _assert_table_pins(document):
    context = document["species"]["Cr"]["context"]
    table = next(item for item in context if item["observation_id"].endswith("table1_redox_reaction_thermodynamics"))
    rows = table["values"]["rows"]
    assert len(rows) == 4
    assert rows[0] == {
        "cation": "Cr",
        "basalt_like_delta_H_kcal_mol": "34.5±13",
        "basalt_like_delta_S_cal_mol": "9.6±7",
        "diopsidic_delta_H_kcal_mol": "28.9±3",
        "diopsidic_delta_S_cal_mol": "6.7±2",
    }
    assert rows[1]["diopsidic_delta_S_cal_mol"] == "17.0¹"
    figures = next(item for item in context if item["observation_id"].endswith("figures_1_2"))
    assert len(figures["values"]["figures"]) == 2
    assert all(item["digitisation"] == "deferred to a figure pass" for item in figures["values"]["figures"])


def test_colson_keedy_table_and_deferred_figures_are_pinned():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_table_pins(document)


def test_colson_keedy_table_cell_mutation_fails_render_pin():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = deepcopy(document)
    table = next(item for item in mutated["species"]["Cr"]["context"] if item["observation_id"].endswith("table1_redox_reaction_thermodynamics"))
    table["values"]["rows"][0]["basalt_like_delta_H_kcal_mol"] = "34.6±13"
    with pytest.raises(AssertionError):
        _assert_table_pins(mutated)
