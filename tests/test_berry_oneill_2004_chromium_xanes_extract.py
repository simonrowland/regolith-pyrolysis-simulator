"""Evidence-class and raw-input lineage pins for Berry & O'Neill (2004)."""

from copy import deepcopy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/berry-oneill-2004-chromium-xanes.yaml"
IDS = (
    "berry_2004_table4_logkprime_ad",
    "berry_2004_table4_logkprime_ad_en",
    "berry_2004_table4_logkprime_ad_fo",
    "berry_2004_table4_logkprime_ad_qz",
    "berry_2004_table4_logkprime_ad_rt10",
    "berry_2004_table4_logkprime_ad_rt20",
    "berry_2004_table4_logkprime_ad_wo",
    "berry_2004_table4_slope_ad",
    "berry_2004_table4_slope_ad_en",
    "berry_2004_table4_slope_ad_fo",
    "berry_2004_table4_slope_ad_qz",
    "berry_2004_table4_slope_ad_wo",
)


def _rows(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }


def _assert_fitted_rows(document: dict) -> None:
    rows = _rows(document)
    for observation_id in IDS:
        row = rows[observation_id]
        assert row["values"]["method_class"] == "measured_reduced"
        derivation = row["derivation"]
        assert "Table 3 Cr2+/SumCr fO2-series data" in derivation["relation"]
        assert "Equation 7" in derivation["relation"]
        assert derivation["raw_inputs"]["state"]["value"] == "not_printed"
        assert derivation["raw_inputs"]["locator"]["table"] == "3"


def test_table4_fit_outputs_are_authors_measured_reductions() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_fitted_rows(document)


def test_measured_reduced_class_pin_rejects_quoted_reversion() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_fitted_rows(document)
    mutated = deepcopy(document)
    _rows(mutated)[IDS[0]]["values"]["method_class"] = "quoted_attributed"
    try:
        _assert_fitted_rows(mutated)
    except AssertionError:
        return
    raise AssertionError("quoted reversion passed the measured_reduced pin")
