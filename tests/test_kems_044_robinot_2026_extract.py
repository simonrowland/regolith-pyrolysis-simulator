"""Evidence-class and model-lineage pins for Robinot et al. (2026)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_file
from simulator.battery.records import AdmissionStatus, EvidenceClass, ValueKind


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kems-044-robinot-2026.yaml"
MODEL_ROWS = (
    "robinot_2026_o2_hsc_expected_1p37_model",
    "robinot_2026_fig1_hsc_figure_only",
)
def _observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for block in document["species"].values()
        for row in block.get("observations", [])
    }


def _assert_model_rows(document: dict) -> None:
    rows = _observations(document)
    for observation_id in MODEL_ROWS:
        row = rows[observation_id]
        assert row["values"]["method_class"] == "model_derived"
        derivation = row["derivation"]
        assert derivation["method"]["state"]["value"] == (
            "Gibbs energy minimization using HSC Chemistry version 10"
        )
        assert derivation["method"]["locator"]["section"].startswith("2.")
        assert derivation["raw_inputs"]["state"]["value"] == "not_printed"
        assert derivation["references"] == [
            "Šeško et al. (2024), EAC-1 composition; the HSC 10 database species data have no cited source in the located method."
        ]


def test_hsc_rows_are_model_derived_and_migrate_with_prose_method() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_model_rows(document)
    assert validate_extract_file(EXTRACT) == []

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert migrator.result.validation.hard_issues == ()
    model = next(
        row for key, row in migrator.result.observations.items()
        if key.endswith(MODEL_ROWS[0])
    )
    assert model.admission.status is AdmissionStatus.ADMITTED
    assert model.value.kind is ValueKind.UNAVAILABLE
    assert model.derivation is not None and model.derivation.has_located_method
    assert model.derivation.has_named_references
    assert model.evidence.class_.value is EvidenceClass.MODEL_DERIVED


def test_model_class_mutation_back_to_quote_fails() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_model_rows(document)

    mutated = deepcopy(document)
    _observations(mutated)[MODEL_ROWS[0]]["values"]["method_class"] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _assert_model_rows(mutated)
