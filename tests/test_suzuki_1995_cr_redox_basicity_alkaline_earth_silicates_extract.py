from copy import deepcopy
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/suzuki-1995-cr-redox-basicity-alkaline-earth-silicates.yaml"
TABLE1_RENDER = {
    "Li2O·2SiO2": ("66", "10.3"),
    "Na2O·2SiO2": ("-101.2", "9.1"),
    "K2O·2SiO2": ("-341.6", "7.4"),
    "Rb2O·2SiO2": ("-451.8", "7.1"),
    "Cs2O·2SiO2": ("-546.8", "6.2"),
    "MgO·2SiO2": ("201", "11.4"),
    "CaO·2SiO2": ("138", "10.9"),
    "BaO·2SiO2": ("-4", "9.9"),
}


def _assert_tables_match_render(document):
    context = document["species"]["Cr"]["context"]
    table1 = context[0]["values"]["points"]
    assert len(table1) == 8
    for point in table1:
        assert (point["E1_2_mV"], point["pO"]) == TABLE1_RENDER[point["material"]]
    table2 = context[1]["values"]["points"]
    assert len(table2) == 6
    assert (table2[0]["SiO2"], table2[0]["K2O"], table2[0]["pO"]) == ("0.794", "0.031", "11.8")
    assert (table2[-1]["SiO2"], table2[-1]["K2O"], table2[-1]["pO"]) == ("0.483", "0.014", "10.2")


def test_suzuki_tables_match_render_and_deferred_figure_records():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_tables_match_render(document)
    figures = document["species"]["Cr"]["context"][2]["values"]["figures"]
    assert [figure["figure"] for figure in figures] == list("12345")
    assert all("axes_as_printed" in figure and "caption" in figure for figure in figures)
    assert document["species"]["Cr"]["context"][2]["values"]["digitisation"] == "deferred to a figure pass"


def test_suzuki_table_render_pin_fails_after_cell_mutation():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = deepcopy(document)
    mutated["species"]["Cr"]["context"][0]["values"]["points"][0]["E1_2_mV"] = "67"
    with pytest.raises(AssertionError):
        _assert_tables_match_render(mutated)
