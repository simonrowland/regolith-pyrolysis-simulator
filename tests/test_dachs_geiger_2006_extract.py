from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/dachs-geiger-2006-pyrope-grossular.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_extract_validation_and_source_fidelity() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []


def test_all_typed_endmember_values_migrate_with_temperature_and_species() -> None:
    migrator = migrate_one_extract(root=ROOT, path=EXTRACT)
    migrator.finalize()
    expected = {
        "dachs_geiger_2006_pyrope_cp_ppms_py1": ("Mg3Al2Si3O12", Quantity.CP, 320.8, 0.9),
        "dachs_geiger_2006_pyrope_entropy_ppms_py1": ("Mg3Al2Si3O12", Quantity.S, 265.94, 0.23),
        "dachs_geiger_2006_pyrope_cp_ppms_py2": ("Mg3Al2Si3O12", Quantity.CP, 322.0, 0.8),
        "dachs_geiger_2006_pyrope_entropy_ppms_py2": ("Mg3Al2Si3O12", Quantity.S, 265.81, 0.22),
        "dachs_geiger_2006_pyrope_cp_ppms_py3": ("Mg3Al2Si3O12", Quantity.CP, 322.4, 0.9),
        "dachs_geiger_2006_pyrope_entropy_ppms_py3": ("Mg3Al2Si3O12", Quantity.S, 264.20, 0.23),
        "dachs_geiger_2006_pyrope_cp_ppms_py4": ("Mg3Al2Si3O12", Quantity.CP, 322.4, 1.0),
        "dachs_geiger_2006_pyrope_cp_hw80": ("Mg3Al2Si3O12", Quantity.CP, 325.31, None),
        "dachs_geiger_2006_pyrope_cp_b96": ("Mg3Al2Si3O12", Quantity.CP, 325.5, None),
        "dachs_geiger_2006_pyrope_entropy_ppms_py4": ("Mg3Al2Si3O12", Quantity.S, 265.01, 0.24),
        "dachs_geiger_2006_grossular_cp_ppms_gr1": ("Ca3Al2Si3O12", Quantity.CP, 325.1, 1.0),
        "dachs_geiger_2006_grossular_entropy_ppms_gr1": ("Ca3Al2Si3O12", Quantity.S, 257.86, 0.26),
        "dachs_geiger_2006_grossular_cp_ppms_gr2": ("Ca3Al2Si3O12", Quantity.CP, 326.5, 1.0),
        "dachs_geiger_2006_grossular_cp_hw80": ("Ca3Al2Si3O12", Quantity.CP, 333.17, None),
        "dachs_geiger_2006_grossular_cp_b96": ("Ca3Al2Si3O12", Quantity.CP, 331.3, None),
        "dachs_geiger_2006_grossular_entropy_ppms_gr2": ("Ca3Al2Si3O12", Quantity.S, 259.47, 0.25),
    }
    expected = {key: row for key, row in expected.items() if row[1] is Quantity.CP}
    assert len(migrator.result.observations) == len(expected)
    for observation_id, (formula, quantity, value, sigma) in expected.items():
        observation = migrator.result.observations[f"{EXTRACT.stem}::{observation_id}"]
        assert observation.identity.species.formula == formula
        assert observation.identity.quantity.value is quantity
        assert observation.identity.temperature_K.value == Decimal("298.15")
        assert observation.value.point == Decimal(str(value))
        if sigma is None:
            assert observation.uncertainty.value is None
        else:
            assert observation.uncertainty.verbatim["value"] == sigma
        assert observation.locator.page == 6
        assert observation.point_conditions["temperature_K"].state.value == Decimal("298.15")
    assert not migrator.result.validation.hard_issues


def test_fidelity_sample_rejects_a_changed_printed_cell() -> None:
    document = _document()
    mutated = deepcopy(document)
    mutated["species"]["Mg3Al2Si3O12"]["observations"][0]["values"]["cp"] = 320.9
    assert any(
        "fidelity" in error.casefold()
        for error in validate_extract_document(
            mutated,
            expected_source_id=EXTRACT.stem,
            pre_policy_ids=set(),
            check_fidelity_match=True,
        )
    )


def test_fit_and_excess_entropy_tables_are_retained_as_context() -> None:
    context = _document()["species"]["Mg3Al2Si3O12"]["context"]
    by_id = {row["observation_id"]: row for row in context}
    table1 = by_id["dachs_geiger_2006_table_1_printed_values_context"]["values"]["printed_rows"]
    table3 = by_id["dachs_geiger_2006_table_3_printed_values_context"]["values"]["printed_rows"]
    table4 = by_id["dachs_geiger_2006_table_4_printed_values_context"]["values"]["printed_rows"]
    table5 = by_id["dachs_geiger_2006_table_5_printed_values_context"]["values"]["printed_rows"]
    table2_entropies = by_id["dachs_geiger_2006_intermediate_solution_entropies"]["values"]
    assert any("8.7622E-05" in cell for row in table1 for cell in row)
    assert any("Py_{10}Gr_{90}" in cell for row in table3 for cell in row)
    assert any("11.8 ± 0.5" in cell for row in table4 for cell in row)
    assert any("1247" in cell for row in table5 for cell in row)
    assert any(row["value"] == "265.81±0.22" for row in table2_entropies["table2_standard_entropies_J_mol_K"])
    assert "no TABLE_CSV asset" in table2_entropies["note"]
    assert "rows" not in table2_entropies
