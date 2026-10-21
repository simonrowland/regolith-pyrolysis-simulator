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
    assert len(table["chromium_mass_percent"]) == 20
    assert table["minus_log10_fO_in_Ni_as_printed"][0] == "0.29"
    assert table["minus_log10_fO_in_Co_as_printed"][-1] == "1.96"
    assert [r["locator"]["figure"] for r in context[3:]] == [str(n) for n in range(1, 8)]
    assert all(r["values"]["digitisation"] == "deferred to a figure pass" for r in context[3:])


def test_render_pins_context_values_and_migration_mutation():
    document = _document()
    _assert_capture(document)
    mutated = deepcopy(document)
    mutated["species"]["O"]["context"][0]["values"]["minus_log10_fO_in_Ni_as_printed"][0] = "0.30"
    with pytest.raises(AssertionError):
        _assert_capture(mutated)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
    assert not migrator.result.observations
