"""Evidence-class pins for the GLTAS model figures in Stolyarova (2013)."""

from copy import deepcopy
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kems-017-stolyarova-2013.yaml"
IDS = (
    "stolyarova_2013_fig2_bao_gltas_contours_model",
    "stolyarova_2013_fig3_b2o3_gltas_contours_model",
    "stolyarova_2013_fig4_sio2_gltas_contours_model",
    "stolyarova_2013_fig5_gibbs_gltas_contours_model",
    "stolyarova_2013_fig6_gibbs_gltas_model_figure_only",
)
TRANSIENT_EQUATION_GATE = {
    "raw_inputs declaration is invalid for model_derived without a located not_printed declaration, method, and named references",
    "model_derived raw_inputs method requires a nonempty value and an equation and physical page locator",
    "derived observation requires derived_from",
}


def _rows(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }


def _assert_model_evidence(document: dict) -> None:
    rows = _rows(document)
    for observation_id in IDS:
        row = rows[observation_id]
        assert row["values"]["method_class"] == "model_derived"
        assert row["derivation"]["method"]["state"]["value"].endswith("GLTAS)")
        assert row["derivation"]["raw_inputs"]["state"]["value"] == "not_printed"
        assert row["derivation"]["references"] == [
            "GLTAS [50], as cited in the figure caption."
        ]


def test_gltas_figure_outputs_are_model_derived() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_model_evidence(document)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert all(
        issue.detail in TRANSIENT_EQUATION_GATE
        and any(observation_id in issue.path for observation_id in IDS)
        for issue in migrator.result.validation.hard_issues
    )


def test_gltas_class_pin_rejects_quoted_reversion() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_model_evidence(document)
    mutated = deepcopy(document)
    rows = _rows(mutated)
    rows[IDS[0]]["values"]["method_class"] = "quoted_attributed"
    try:
        _assert_model_evidence(mutated)
    except AssertionError:
        return
    raise AssertionError("quoted reversion passed the model_derived pin")
