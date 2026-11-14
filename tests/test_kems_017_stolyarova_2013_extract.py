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
PHYSICAL_PAGES = {
    IDS[0]: (6, 5),
    IDS[1]: (7, 6),
    IDS[2]: (7, 6),
    IDS[3]: (8, 7),
    IDS[4]: (9, 8),
}


def _rows(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }


def _assert_figure_only_model_refusals(document: dict) -> None:
    rows = _rows(document)
    for observation_id in IDS:
        row = rows[observation_id]
        values = row["values"]
        assert values["method_class"] == "typed_refusal"
        assert values["method_class_as_printed"] == "model_derived"
        assert values["admission_status"] == "typed_refusal"
        assert "Figure-only model" in values["refusal_reason"]
        assert "GLTAS" in values["method_as_printed"]
        assert values["method_locator"]["page"] == PHYSICAL_PAGES[observation_id][0]
        assert values["method_locator"]["pdf_page_index"] == PHYSICAL_PAGES[observation_id][1]
        assert values["method_locator"]["figure"] == row["locator"]["figure"]
        assert values["method_references_as_printed"] == ["Stolyarova and Shilov [50]"]
        if observation_id != IDS[4]:
            assert values["method_references_locator"]["published_page"] == "65"
            assert values["method_references_locator"]["paragraph"] == (
                "the other computational procedure was suggested [50]."
            )
        assert "derivation" not in row
        if observation_id == IDS[4]:
            assert values["quote"] == values["method_as_printed"]
        else:
            assert "solid lines are results of modeling using GLTAS" in values["quote"]


def test_gltas_figure_outputs_remain_unavailable_without_sampled_points() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_figure_only_model_refusals(document)
    rows = _rows(document)
    assert rows[IDS[0]]["values"]["contour_levels_as_printed"] == [
        "-4",
        "-5",
        "-8",
        "-11",
    ]
    assert rows[IDS[4]]["values"]["completeness_note"] == (
        "Curves are figure-only; no points were digitised."
    )

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert not migrator.result.validation.hard_issues


def test_gltas_class_pin_rejects_figure_refusal_reversion() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_figure_only_model_refusals(document)
    mutated = deepcopy(document)
    rows = _rows(mutated)
    rows[IDS[0]]["values"]["method_class"] = "model_derived"
    try:
        _assert_figure_only_model_refusals(mutated)
    except AssertionError:
        return
    raise AssertionError("a calculated figure line was admitted as a sampled value")
