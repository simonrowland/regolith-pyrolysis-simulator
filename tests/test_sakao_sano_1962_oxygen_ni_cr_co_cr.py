"""Context capture and deferred figures for Sakao & Sano (1962)."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/sakao-sano-1962-oxygen-ni-cr-co-cr.yaml"


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _assert_capture(document):
    context = document["species"]["O"]["context"]
    table = context[0]["values"]
    assert table["schema_gap"] == "t-1394"
    rows = table["table_rows"]
    assert len(rows) == 20
    assert sum(len(row["cells"]) for row in rows) == 60
    assert rows[0]["cells"][1] == {
        "column": "-log fO in Ni",
        "composition": "liquid Ni-Cr",
        "value_as_printed": "0.29",
    }
    assert rows[-1]["cells"][2]["value_as_printed"] == "1.96"
    fits = context[1]["values"]
    assert fits["equations_as_printed"][0] == "Ni-Cr-O: log fO(Ni) = 11.96 NCr - 15.73 N_Cr^0.8"
    assert fits["condition_as_printed"] == "1600 °C; fit range N_Cr < 0.3"
    assert context[2]["values"]["values_as_printed"] == "e_O(Ni)^Cr = -0.20; e_O(Co)^Cr = -0.17; at 1600 °C"
    assert [r["locator"]["figure"] for r in context[3:]] == [str(n) for n in range(1, 8)]
    assert all(r["values"]["digitisation"] == "deferred to a figure pass" for r in context[3:])


def test_render_pins_context_values_and_migration_mutation():
    document = _document()
    _assert_capture(document)
    mutated = deepcopy(document)
    mutated["species"]["O"]["context"][0]["values"]["table_rows"][0]["cells"][1]["value_as_printed"] = "0.30"
    with pytest.raises(AssertionError):
        _assert_capture(mutated)
    mutated = deepcopy(document)
    mutated["species"]["O"]["context"][1]["values"]["equations_as_printed"][0] = "Ni-Cr-O: log fO(Ni) = 11.96 NCr - 15.73 N_Cr^0.6"
    with pytest.raises(AssertionError):
        _assert_capture(mutated)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
    assert not migrator.result.observations
