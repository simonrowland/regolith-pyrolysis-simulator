from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.enums import ValueKind
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_file


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/mcgill-na-fe-o-critical-assessment.yaml"
PDF_PAGE = 5


def _rows() -> dict[str, dict]:
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    return {row["observation_id"]: row for row in doc["species"]["NaFeO2"]["observations"]}


def test_nafeo2_table_ii_render_values_and_comparison_are_pinned() -> None:
    assert validate_extract_file(EXTRACT, check_fidelity_match=True) == []
    rows = _rows()
    assert len(rows) == 3
    pins = {
        "mcgill16_nafeo2_quoted_delta_fH": "-698.02",
        "mcgill16_nafeo2_quoted_entropy": "88.34",
        "mcgill16_nafeo2_fusion_temperature_exp": "1620",
    }
    for observation_id, expected in pins.items():
        row = rows[observation_id]
        value_key = "T_m_K" if observation_id.endswith("fusion_temperature_exp") else "value"
        assert row["values"][value_key] == expected
        expected_page, expected_table = (7, "IV") if observation_id.endswith("fusion_temperature_exp") else (PDF_PAGE, "II")
        assert row["locator"]["page"] == expected_page
        assert row["locator"]["table"] == expected_table
    context = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))["species"]["NaFeO2"]["context"][0]
    transitions = context["values"]["polymorph_transition_enthalpies_as_printed"]
    assert [(row["transition"], row["value"], row["temperature_K"]) for row in transitions] == [
        ("alpha -> beta", "41.84", "870"),
        ("beta -> gamma", "2177", "1270"),
    ]
    assert context["values"]["optimized_thermochemistry_as_printed"] == {
        "formation_enthalpy_298_kJ_mol": "-697.350",
        "standard_entropy_298_J_mol_K": "88.341",
        "fusion_temperature_K": "1620",
    }


def test_isolated_migration_keeps_numeric_rows_and_transition_schema_gap() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    observations = migrator.result.observations
    assert len(observations) == 3
    assert not migrator.result.validation.hard_issues
    assert all(row.identity.species.phase.is_value for row in observations.values())
    assert {
        row_id for row_id, row in observations.items()
        if row.value.kind is ValueKind.UNAVAILABLE
    } == set()
    assert all(row.identity.quantity.is_value for row in observations.values())
    assert all(
        row.point_conditions["temperature_K"].state.is_value
        for row in observations.values()
    )
