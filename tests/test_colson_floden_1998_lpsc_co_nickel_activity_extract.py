from copy import deepcopy
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/colson-floden-1998-lpsc-co-nickel-activity.yaml"


def _assert_abstract_pins(document):
    context = document["species"]["Ni"]["context"]
    conditions = next(item for item in context if item["observation_id"].endswith("experimental_conditions"))["values"]
    assert conditions["composition_wt_pct"] == {"SiO2": "55.5", "CaO": "25.9", "MgO": "18.6", "NiO": "0.3"}
    assert conditions["temperature_C"] == ["1440", "1500", "1575"]
    assert conditions["oxygen_fugacity_range_as_printed"] == ["1.8 × 10^-3", "3.0 × 10^-10"]
    figures = next(item for item in context if item["observation_id"].endswith("figures_1_to_4"))
    assert [item["figure"] for item in figures["values"]["figures"]] == ["1", "2", "3", "4"]
    assert all(item["digitisation"] == "deferred to a figure pass" for item in figures["values"]["figures"])


def test_colson_floden_conditions_and_deferred_figures_are_pinned():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_abstract_pins(document)


def test_colson_floden_temperature_mutation_fails_pin():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = deepcopy(document)
    conditions = mutated["species"]["Ni"]["context"][0]["values"]
    conditions["temperature_C"][0] = "1441"
    with pytest.raises(AssertionError):
        _assert_abstract_pins(mutated)
