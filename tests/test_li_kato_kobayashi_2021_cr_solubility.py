"""Render-checked setup table and deferred-figure guard for Li et al. (2021)."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/li-kato-kobayashi-2021-cr-solubility.yaml"


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _assert_render_pins(document):
    context = document["species"]["CrOx"]["context"]
    table1, table2 = context[:2]
    assert len(table1["values"]["rows"]) == 6
    assert table1["values"]["rows"][0] == {"CaO": "30.0", "SiO2": "60.0", "Cr2O3": "10.0", "mass_percent_CaO_to_SiO2": "0.5"}
    assert table1["values"]["rows"][-1]["Cr2O3"] == "3.0"
    assert len(table2["values"]["rows"]) == 4
    assert table2["values"]["rows"][2]["PCO_to_PCO2"] == "1 000/1"
    assert context[4]["locator"]["page"] == 3
    assert context[6]["locator"]["page"] == 5


def test_render_pins_mutation_and_isolated_migration():
    document = _document()
    _assert_render_pins(document)
    mutated = deepcopy(document)
    mutated["species"]["CrOx"]["context"][0]["values"]["rows"][0]["CaO"] = "30.1"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
    assert not migrator.result.observations
