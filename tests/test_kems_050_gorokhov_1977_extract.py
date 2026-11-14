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


def _assert_refused_model_result(document: dict) -> None:
    row = next(
        row
        for row in document["species"]["CrO"]["observations"]
        if row["observation_id"] == ROW_ID
    )
    values = row["values"]
    assert values["method_class"] == "typed_refusal"
    assert values["method_class_as_printed"] == "model_derived"
    assert values["admission_status"] == "typed_refusal"
    assert "page-located prose method from that report" in values["reason"]
    assert values["value_as_printed"] == "88.7 ± 38"
    assert values["delta_rH0_II_law_kJ_mol"] == 88.7
    assert values["method_as_printed"] == "То же, AlO + Cr = Al + CrO, 2242-2690 K, 12 точек"
    assert values["method_locator_as_printed"]["table"] == "Cr.T3"
    assert values["references_as_printed"] == [
        "Gorokhov, Gusarov et al. (1977), Report 43-77, record [77ГОР/ГУС]"
    ]


def test_compilation_reprocessing_keeps_value_with_typed_method_refusal() -> None:
    document = _document()
    _assert_refused_model_result(document)

    mutated = deepcopy(document)
    row = next(
        row
        for row in mutated["species"]["CrO"]["observations"]
        if row["observation_id"] == ROW_ID
    )
    row["values"]["method_class_as_printed"] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _assert_refused_model_result(mutated)
