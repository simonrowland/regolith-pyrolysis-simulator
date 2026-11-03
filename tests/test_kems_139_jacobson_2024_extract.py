from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from tests.battery.factories import migrate_one_extract
from tests.battery.sc456 import assert_only_pending_equation_issues


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "kems-139-jacobson-2024"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"


def _observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species["observations"]
    }


def _assert_sc456_evidence(document: dict) -> None:
    rows = _observations(document)

    factsage = rows["jacobson_2024_t1_factsage_2500k_predicted_psat"]
    assert factsage["values"]["method_class"] == "model_derived"
    assert len(factsage["values"]["points"]) == 21
    assert factsage["values"]["table_csv"] == "tables/kems-139-jacobson-2024/t1.csv"
    assert factsage["values"]["quote"].startswith(
        "Predicted vapor species of a lunar basalt at 2500 K"
    )
    assert factsage["derivation"]["method"]["locator"]["section"] == (
        "2.4.2 Ion current and ion counting measurements in the mass spectrum of a vapor; "
        "item 6: Comparison to calculated/estimated vapor pressures for the condensed phase in the Knudsen cell"
    )
    assert factsage["derivation"]["references"] == [
        "Bale et al. [115]",
        "Snyder et al. [117]",
    ]
    assert factsage["derivation"]["raw_inputs"]["state"]["value"] == "not_printed"

    gold = rows["jacobson_2024_fig16_au_vant_hoff_figure_only"]
    assert gold["values"]["method_class"] == "measured_reduced"
    assert gold["derivation"]["method"]["locator"]["equation"] == "23b"
    assert gold["derivation"]["raw_inputs"]["state"]["value"] == "not_printed"


def test_sc456_classes_and_lineage_are_pinned_with_isolated_migration() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_sc456_evidence(document)

    reverted = deepcopy(document)
    rows = _observations(reverted)
    rows["jacobson_2024_t1_factsage_2500k_predicted_psat"]["values"][
        "method_class"
    ] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _assert_sc456_evidence(reverted)

    reverted = deepcopy(document)
    _observations(reverted)["jacobson_2024_fig16_au_vant_hoff_figure_only"][
        "values"
    ]["method_class"] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _assert_sc456_evidence(reverted)

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert not result.registry_issues
    # t-1610 will make the prose method sufficient; until then this is the
    # only permitted issue for the author-generated FactSage result.
    assert_only_pending_equation_issues(
        result.validation,
        observation_id_fragment="jacobson_2024_t1_factsage_2500k_predicted_psat",
    )
