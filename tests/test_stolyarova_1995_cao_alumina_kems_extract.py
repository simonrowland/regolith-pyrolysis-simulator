"""Pins the authors' KEMS partial-pressure reductions in Stolyarova et al. (1995)."""

from copy import deepcopy
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/stolyarova-1995-cao-alumina-kems.yaml"
IDS = (
    "stolyarova_1995_o2_partial_pressure_table2",
    "stolyarova_1995_o_partial_pressure_eqn1_table2",
    "stolyarova_1995_o_partial_pressure_eqn2_table2",
)


def _rows(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }


def _assert_measured_reductions(document: dict) -> None:
    rows = _rows(document)
    for observation_id in IDS:
        row = rows[observation_id]
        assert row["values"]["method_class"] == "measured_reduced"
        assert row["values"]["evidence_class"] == "measured_reduced"
        assert row["derivation"]["raw_inputs"]["state"]["value"] == "not_printed"
        assert row["derivation"]["raw_inputs"]["locator"]


def test_partial_pressure_rows_are_authors_reductions_and_migrate_cleanly() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_measured_reductions(document)
    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues


def test_measured_reduced_pin_rejects_quoted_reversion() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_measured_reductions(document)
    mutated = deepcopy(document)
    _rows(mutated)[IDS[0]]["values"]["method_class"] = "quoted_attributed"
    try:
        _assert_measured_reductions(mutated)
    except AssertionError:
        return
    raise AssertionError("quoted reversion passed the measured_reduced pin")
