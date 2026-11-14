"""Model-source pins for Shornikov (2024) K2O–P4O10 context values."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2024-alkali-phosphate.yaml"
MODEL_ROWS = (
    "shornikov_2024_k2o_po2_o2_bound",
    "shornikov_2024_k2o_k_pressure_bound",
)


def _contexts(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for row in document["species"]["K2O"]["context"]
    }


def _assert_model_rows(document: dict) -> None:
    rows = _contexts(document)
    for observation_id in MODEL_ROWS:
        row = rows[observation_id]
        assert row["values"]["method_class"] == "model_derived"
        derivation = row["derivation"]
        assert derivation["method"]["state"]["value"] == "semi-empirical model"
        assert derivation["method"]["locator"]["published_page"] == 71
        assert derivation["raw_inputs"]["state"]["value"] == "not_printed"
        assert derivation["references"] == [
            "Glushko et al. (1978–1982)",
            "Alikhanyan et al. (1975)",
            "Rudny et al. (1986)",
        ]


def test_model_rows_have_source_lineage_and_migrate_cleanly() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_model_rows(document)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()


def test_model_class_mutation_back_to_quote_fails() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_model_rows(document)

    mutated = deepcopy(document)
    rows = _contexts(mutated)
    rows[MODEL_ROWS[0]]["values"]["method_class"] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _assert_model_rows(mutated)
