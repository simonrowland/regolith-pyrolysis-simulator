from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "kems-138-bischof-2023"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"


def _observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species["observations"]
    }


def _assert_sc456_evidence(document: dict) -> None:
    rows = _observations(document)
    attribution = "Petsalakis et al. [41] and FACTSG, as named by Table 10."
    for observation_id in (
        "bischof_2023_table10_gao_ab_initio",
        "bischof_2023_table10_gao_ab_initio_deep4_quoted",
    ):
        values = rows[observation_id]["values"]
        assert values["method_class"] == "quoted_attributed"
        assert values["attribution"] == attribution

    model = rows["bischof_2023_table13_ga2o_2nd_law_factsage"]
    assert model["values"]["method_class"] == "model_derived"
    assert model["derivation"]["method"]["locator"]["equation"] == "27–30"
    assert model["derivation"]["references"] == [
        "Hultgren et al. [39]",
        "Chaplygin [37]",
        "Medvedev et al. [38]",
    ]
    assert model["derivation"]["raw_inputs"]["state"]["value"] == "not_printed"


def test_sc456_attributions_and_model_class_are_pinned_with_isolated_migration() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_sc456_evidence(document)

    reverted = deepcopy(document)
    _observations(reverted)["bischof_2023_table13_ga2o_2nd_law_factsage"]["values"][
        "method_class"
    ] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _assert_sc456_evidence(reverted)

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert not result.registry_issues
    assert result.validation is not None
    assert not result.validation.hard_issues
