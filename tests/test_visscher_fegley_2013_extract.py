"""Evidence-class pins for Visscher and Fegley (2013)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/visscher-fegley-2013-debris-disks.yaml"
MODEL_RESULT_IDS = (
    "visscher_2013_sio_partial_pressure_equilibrium",
    "visscher_2013_low_temperature_volatile_dominance",
    "visscher_2013_low_temperature_volatile_dominance_zinc",
    "visscher_2013_saturated_vapor_oxygen_fugacity",
    "visscher_2013_total_vapor_pressure_sum",
    "visscher_2013_bse_total_vapor_pressure_fit",
    "visscher_2013_bsm_total_vapor_pressure_fit",
    "visscher_2013_water_ratio_constraint",
    "visscher_2013_carbon_dioxide_ratio_constraint",
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _rows(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for collection in (species.get("observations", []), species.get("context", []))
        for row in collection
    }


def _assert_model_result_classes(document: dict) -> None:
    rows = _rows(document)
    assert set(MODEL_RESULT_IDS) <= rows.keys()
    for observation_id in MODEL_RESULT_IDS:
        row = rows[observation_id]
        values = row["values"]
        derivation = row["derivation"]
        assert values["method_class"] == "model_derived"
        assert derivation["raw_inputs"]["state"]["value"] == "not_printed"
        assert derivation["raw_inputs"]["locator"]["section"] == "2.1. Chemical Model"
        assert derivation["method"]["state"]["value"]
        assert derivation["method"]["locator"]["page"] > 0
        assert derivation["method"]["locator"]["section"]
        assert derivation["references"] == [
            "Fegley, B., & Cameron, A. G. W. 1987, Earth and Planetary Science Letters, 82, 207.",
            "Schaefer, L., & Fegley, B. 2004, Icarus, 169, 216.",
        ]


def test_magma_results_are_pinned_as_authors_model_outputs() -> None:
    document = _document()
    _assert_model_result_classes(document)

    pure_silica = _rows(document)["visscher_2013_sio2_pure_melt_vapor_pressure_fit"]
    assert pure_silica["values"]["method_class"] == "quoted_attributed"
    assert pure_silica["values"]["attribution"] == (
        "Fegley and Schaefer (2012), as cited by Visscher and Fegley in Section 3.2."
    )

    oxygen_context = _rows(document)["visscher_2013_o2_vapor_fraction"]
    assert oxygen_context["values"]["method_class"] == "quoted_attributed"
    assert oxygen_context["quote"] == "Visscher and Fegley (2013), MAGMA equilibrium calculation"


def test_model_result_class_pin_detects_a_flip_back_to_quote() -> None:
    document = deepcopy(_document())
    rows = _rows(document)
    rows["visscher_2013_bse_total_vapor_pressure_fit"]["values"][
        "method_class"
    ] = "quoted_attributed"

    with pytest.raises(AssertionError):
        _assert_model_result_classes(document)
