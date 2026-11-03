from copy import deepcopy
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/cardiff-2007-vacuum-pyrolysis-gsfc.yaml"


def _assert_model_rows(document: dict) -> None:
    rows = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }
    for observation_id in (
        "cardiff_2007_fig6_hsc_1e-1_Torr_figure_only",
        "cardiff_2007_fig7_hsc_1e-7_Torr_figure_only",
    ):
        row = rows[observation_id]
        assert row["values"]["method_class"] == "model_derived"
        derivation = row["derivation"]
        assert derivation["raw_inputs"]["state"]["value"] == "not_printed"
        assert derivation["method"]["state"]["value"] == (
            "Gibbs free-energy minimization with the HSC analysis package"
        )
        assert derivation["method"]["locator"]["section"] == "MODELING"
        assert derivation["references"][0].startswith("none_printed:")


def test_hsc_equilibrium_outputs_are_model_derived_and_mutation_sensitive():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_model_rows(document)

    mutated = deepcopy(document)
    row = next(
        row
        for species in mutated["species"].values()
        for row in species.get("observations", [])
        if row["observation_id"] == "cardiff_2007_fig6_hsc_1e-1_Torr_figure_only"
    )
    row["values"]["method_class"] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _assert_model_rows(mutated)
