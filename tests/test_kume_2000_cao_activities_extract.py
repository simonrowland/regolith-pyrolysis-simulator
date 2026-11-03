"""Focused source-class and lineage pins for Kume et al. (2000)."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kume-2000-cao-activities.yaml"
SAMPLE = "kume_2000_table4_sample_301_alo1_5_activity"


def _assert_reduced(row: dict) -> None:
    values = row["values"]
    assert values["method_class"] == "measured_reduced"
    assert values["evidence_class"] == "measured_reduced"
    assert row["derived_from"] == [
        "kume_2000_table4_sample_301_metal_composition",
        "kume_2000_table4_sample_301_sio2_activity",
    ]
    assert row["derivation"]["inputs"] == row["derived_from"]
    assert "exchange relation" in row["derivation"]["relation"]


def test_table4_activity_is_authors_reduced_result_with_inputs() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    row = next(
        observation
        for species in document["species"].values()
        for observation in species["observations"]
        if observation["observation_id"] == SAMPLE
    )
    _assert_reduced(row)


def test_table4_activity_class_pin_rejects_quoted_reversion() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    row = next(
        observation
        for species in document["species"].values()
        for observation in species["observations"]
        if observation["observation_id"] == SAMPLE
    )
    mutated = {**row, "values": {**row["values"], "method_class": "quoted_attributed", "evidence_class": "quoted_attributed"}}
    try:
        _assert_reduced(mutated)
    except AssertionError:
        return
    raise AssertionError("quoted_attributed mutation passed the measured_reduced pin")
