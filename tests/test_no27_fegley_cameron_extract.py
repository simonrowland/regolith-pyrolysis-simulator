"""Evidence-class pins for Fegley and Cameron (1987) model outputs."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/no27-fegley-cameron-1987-epsl.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _assert_model_lineage(document: dict) -> None:
    observations = document["species"]["SiO2"]["observations"]
    for observation_id in (
        "fegley_cameron_1987_table4_predicted_compositions",
        "fegley_cameron_1987_printed_model_results",
    ):
        row = next(row for row in observations if row["observation_id"] == observation_id)
        assert row["values"]["evidence_class"] == "model_derived"
        assert row["values"]["method_class"] == "model_derived"
        derivation = row["values"]["derivation"]
        assert derivation["raw_inputs"]["state"]["value"] == "not_printed"
        assert derivation["method"]["locator"]["section"] == (
            "2.1 Description of thermodynamic model"
        )
        assert "MAGMA" in derivation["method"]["state"]["value"]
        assert derivation["references"] == [
            "Hastie and co-workers [14–17] for the IMCC model",
            "Anders and Ebihara [18] for solar-system abundances",
            "Thermochemical data sources cited in Tables 1–2 [19–26]",
        ]


def test_model_results_are_pinned_as_author_calculations() -> None:
    document = _document()
    _assert_model_lineage(document)

    mutated = deepcopy(document)
    row = next(
        row
        for row in mutated["species"]["SiO2"]["observations"]
        if row["observation_id"] == "fegley_cameron_1987_table4_predicted_compositions"
    )
    row["values"]["method_class"] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _assert_model_lineage(mutated)
