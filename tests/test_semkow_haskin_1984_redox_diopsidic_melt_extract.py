from pathlib import Path
from copy import deepcopy

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/semkow-haskin-1984-redox-diopsidic-melt.yaml"
RENDER_ROWS = {
    "Fe": ("-0.218 ± 0.014", "0.054 ± 0.010", "10.0 ± 0.7", "-56.0 ± 4.1", "-38.3 ± 2.0"),
    "Co": ("-0.208 ± 0.014", "0.062 ± 0.012", "9.6 ± 0.7", "-57.5 ± 7.4", "-38.9 ± 3.9"),
    "Ni": ("-0.330 ± 0.004", "0.012 ± 0.001", "15.2 ± 0.2", "-47.9 ± 3.6", "-36.6 ± 2.0"),
    "Zn": ("-0.128 ± 0.004", "0.180 ± 0.011", "5.9 ± 0.2", "-61.2 ± 3.8", "-38.9 ± 2.1"),
}


def _assert_table_matches_render(document):
    context = document["species"]["paper_context"]["context"]
    table = context[0]
    assert table["locator"]["page"] == 2
    points = table["values"]["points"]
    assert len(points) == 4
    for point in points:
        assert (
            point["E0_V"],
            point["K_mol_L_atm"],
            point["delta_G0_kcal_mol"],
            point["delta_H0_kcal_mol"],
            point["delta_S0_eu"],
        ) == RENDER_ROWS[point["species"]]
    assert context[1]["values"]["value"] == "-0.73 ± 0.02"


def test_semkow_table1_render_transcription_and_context_disposition():
    _assert_table_matches_render(yaml.safe_load(EXTRACT.read_text(encoding="utf-8")))


def test_semkow_render_pin_is_sensitive_to_a_changed_cell():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = deepcopy(document)
    mutated["species"]["paper_context"]["context"][0]["values"]["points"][0]["E0_V"] = "-0.218 ± 0.041"
    with pytest.raises(AssertionError):
        _assert_table_matches_render(mutated)
