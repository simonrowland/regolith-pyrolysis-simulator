"""Evidence-class pin for the Gorokhov second-law compilation result."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kems-050-gorokhov-1977.yaml"
ROW_ID = "gorokhov_1977_t3_cro_alo_exchange_second_law"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _assert_model_result(document: dict) -> None:
    row = next(
        row
        for row in document["species"]["CrO"]["observations"]
        if row["observation_id"] == ROW_ID
    )
    assert row["values"]["method_class"] == "model_derived"
    derivation = row["values"]["derivation"]
    assert derivation["raw_inputs"]["state"]["value"] == "not_printed"
    assert derivation["raw_inputs"]["locator"]["section"].startswith("[77ГОР/ГУС]")
    assert derivation["method"]["locator"]["page"] == "HTML table Cr.T3"
    assert "II-law" in derivation["method"]["state"]["value"]
    assert derivation["references"] == [
        "Gorokhov, Gusarov et al. (1977), Report 43-77, record [77ГОР/ГУС]"
    ]


def test_compilation_reprocessing_is_pinned_as_model_derived() -> None:
    document = _document()
    _assert_model_result(document)

    mutated = deepcopy(document)
    row = next(
        row
        for row in mutated["species"]["CrO"]["observations"]
        if row["observation_id"] == ROW_ID
    )
    row["values"]["method_class"] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _assert_model_result(mutated)
