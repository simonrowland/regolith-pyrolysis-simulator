"""Evidence-class and model-lineage pins for Robinot et al. (2026)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_file


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kems-044-robinot-2026.yaml"
MODEL_ROWS = (
    "robinot_2026_o2_hsc_expected_1p37_model",
    "robinot_2026_fig1_hsc_figure_only",
)
COMPOSITION_INPUT_ID = "robinot_2026_eac1_composition_quoted_sesko"


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
        assert "Thermodynamic analysis was conducted" in derivation["method"]["locator"]["note"]
        assert derivation["inputs"] == [COMPOSITION_INPUT_ID]
        assert row["derived_from"] == [COMPOSITION_INPUT_ID]
        assert "raw_inputs" not in derivation
        assert derivation["references"] == [
            "Šeško et al. (2024), EAC-1 composition; the HSC 10 database species data have no cited source in the located method."
        ]
    output_values = rows[MODEL_ROWS[0]]["values"]
    assert output_values["quantity"] == "yield_fraction"
    assert output_values["value_as_printed"] == "around 1.37 %"
    assert output_values["approximate"] is True
    assert output_values["admission_status"] == "admitted"
    assert rows[MODEL_ROWS[1]]["values"]["admission_status"] == "figure_only"


def test_hsc_rows_are_model_derived_with_printed_method_and_lineage() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_model_rows(document)
    assert validate_extract_file(EXTRACT) == []

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert migrator.result.validation.hard_issues == ()
    output_id = f"{EXTRACT.stem}::robinot_2026_o2_hsc_expected_1p37_model"
    output = migrator.result.observations[output_id]
    assert output.value.kind.value == "point"
    assert str(output.value.point) == "0.0137"
    assert output.value.approximate
    assert output.identity.temperature_K.is_value
    assert str(output.identity.temperature_K.value) == "2073.15"
    pressure = output.point_conditions["total_pressure_Pa"]
    assert pressure.state.is_value
    assert str(pressure.state.value) == "1000"
    assert pressure.locator is not None


def test_model_class_mutation_back_to_quote_fails() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_model_rows(document)

    mutated = deepcopy(document)
    _observations(mutated)[MODEL_ROWS[0]]["values"]["method_class"] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _assert_model_rows(mutated)
