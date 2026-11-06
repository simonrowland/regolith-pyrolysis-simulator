from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.records import ValueKind
from tests.battery.test_migrate import _migrate_real_extract
from tools.validate_literature_extracts import check_all_fidelity_samples_match

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/huang-furukawa-aoto-2003-sodium-iron-oxides-phase-diagram.yaml"
TABLE_VALUES = {
    ("NaFeO2", "delta_fG"): "-639.98",
    ("NaFeO2", "delta_fH"): "-698.18",
    ("NaFeO2", "S"): "88.3",
    ("Na4FeO3", "delta_fG"): "-1107.52",
    ("Na4FeO3", "delta_fH"): "-1206.13",
    ("Na4FeO3", "S"): "208.9",
    ("Na3FeO3", "delta_fG"): "-1068.3",
    ("Na3FeO3", "delta_fH"): "-1162.6",
    ("Na3FeO3", "S"): "172.0",
    ("Na5FeO4", "delta_fG"): "-1462.7",
    ("Na5FeO4", "delta_fH"): "-1596.0",
    ("Na5FeO4", "S"): "246.3",
    ("Na8Fe2O7", "delta_fG"): "-2524.3",
    ("Na8Fe2O7", "delta_fH"): "-2746.0",
    ("Na8Fe2O7", "S"): "438.1",
}
TABLE_COEFFICIENTS = {
    "NaFeO2": ({"a": "100.77", "b": "0.0186", "c": "1479000"}, {"g1": "723394", "g2": "455.159", "g3": "80.55", "g4": "0.006653", "g5": "0.0"}),
    "Na4FeO3": ({"a": "212.49", "b": "0.0383", "c": "3280000"}, {"g1": "1282201", "g2": "1244.166", "g3": "212.49", "g4": "0.019155", "g5": "1642000"}),
    "Na3FeO3": ({"a": "181.7", "b": "0.03339", "c": "2967000"}, {"g1": "1228246", "g2": "1071.530", "g3": "181.69", "g4": "0.016695", "g5": "1483500"}),
    "Na5FeO4": ({"a": "262.61", "b": "0.04818", "c": "4455000"}, {"g1": "1691381", "g2": "1551.917", "g3": "262.60", "g4": "0.024000", "g5": "2228000"}),
    "Na8Fe2O7": ({"a": "444.3", "b": "0.08156", "c": "7422000"}, {"g1": "2906986", "g2": "2603.706", "g3": "444.3", "g4": "0.04078", "g5": "3711000"}),
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_table_1_render_pins_and_numeric_cell_count() -> None:
    document = _document()
    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []
    assert len(TABLE_VALUES) == 15
    assert len(document["fidelity_samples"]) >= 3
    for sample in document["fidelity_samples"]:
        assert sample["locator"]["page"] == 2
        assert sample["locator"]["published_page"] == 68
        assert sample["locator"]["table"] == "1"
    mutated = copy.deepcopy(document)
    mutated["species"]["NaFeO2"]["observations"][0]["values"]["value"] = "-639.97"
    assert check_all_fidelity_samples_match(mutated, label="mutated Table 1")

    coefficient_context = {
        species: next(
            row
            for row in payload["context"]
            if row["observation_id"] == f"huang2003p2_table1_{species}_function_coefficients"
        )["values"]
        for species, payload in document["species"].items()
        if species in {"NaFeO2", "Na4FeO3", "Na3FeO3", "Na5FeO4", "Na8Fe2O7"}
    }
    for species, (heat_capacity, gibbs) in TABLE_COEFFICIENTS.items():
        context = coefficient_context[species]
        assert context["heat_capacity_coefficients_as_printed"] == heat_capacity
        assert context["gibbs_function_coefficients_as_printed"] == gibbs
    na3_rows = {
        row["values"]["quantity"]: row
        for row in document["species"]["Na3FeO3"]["observations"]
    }
    assert na3_rows["delta_fH"]["values"]["method_class"] == "model_derived"
    assert na3_rows["delta_fH"]["derivation"]["method"]["locator"]["equation"] == "3"
    assert na3_rows["delta_fH"]["derived_from"] == [
        "huang2003p2_Na3FeO3_table1_delta_fG",
        "huang2003p2_Na3FeO3_table1_entropy",
    ]
    assert na3_rows["S"]["values"]["method_class"] == "author_estimate"
    assert na3_rows["delta_fG"]["values"]["method_class"] == "quoted_attributed"
    reactions = next(
        row
        for row in document["species"]["Na(l)"]["context"]
        if row["observation_id"] == "huang2003p2_reactions_7_10_measured"
    )["values"]
    assert reactions["method_class"] == "measured_direct"
    assert len(reactions["reactions_as_printed"]) == 4
    na8_h = next(
        row
        for row in document["species"]["Na8Fe2O7"]["observations"]
        if row["values"]["quantity"] == "delta_fH"
    )
    assert na8_h["standard_state_as_printed"].startswith("Standard formation enthalpy")
    na8_g = next(
        row
        for row in document["species"]["Na8Fe2O7"]["observations"]
        if row["values"]["quantity"] == "delta_fG"
    )
    assert na8_g["values"]["method_class"] == "model_derived"
    assert na8_g["values"]["printed_discrepancy"].startswith(
        "In section 2.2.5 the prose says ΔfH°(Na8Fe2O7) was calculated"
    )
    assert "ΔfH°(298) = -2524.3 kJ mol−1" in na8_g["values"]["printed_discrepancy"]
    assert "ΔfG°(Na4FeO3) = −1212202 + 351.10×T" in na8_g["values"]["derivation_note"]
    assert "does not evaluate" in na8_g["values"]["derivation_note"]
    assert "not ΔfS°" in na8_g["values"]["derivation_note"]
    assert na8_g["derivation"]["method"]["locator"]["page"] == 3
    assert na8_g["derived_from"] == [
        "huang2003p2_Na8Fe2O7_table1_delta_fH",
        "huang2003p2_Na8Fe2O7_table1_entropy",
    ]
    context_rows = {
        row["observation_id"]: row
        for payload in document["species"].values()
        for row in payload.get("context", [])
    }
    oxygen_limit = context_rows["huang2003p2_Na4FeO3_oxygen_potential_limit"]
    assert oxygen_limit["values"]["value_as_printed"] == "about −536"
    assert oxygen_limit["values"]["temperature_as_printed"] == "800 K"
    stability = context_rows["huang2003p2_Na8Fe2O7_temperature_stability_prose"]
    assert stability["values"]["appears_over_temperature_as_printed"] == "about 637 K"
    assert stability["values"]["tends_to_replace_Na5FeO4_above_temperature_as_printed"] == "about 944 K"
    assert len(TABLE_VALUES) + sum(
        len(heat_capacity) + len(gibbs)
        for heat_capacity, gibbs in TABLE_COEFFICIENTS.values()
    ) == 55


def test_isolated_migration_binds_all_15_table_values_and_conditions(tmp_path: Path) -> None:
    document = _document()
    result = _migrate_real_extract(
        tmp_path,
        EXTRACT.name,
        write=True,
        extract_doc=document,
    )
    assert result.validation is not None
    assert result.validation.hard_issues == (), result.validation.hard_issues
    assert len(result.observations) == 15
    migrated = list(result.observations.values())
    assert all(row.value.kind is ValueKind.POINT for row in migrated), [(row.observation_id, row.value) for row in migrated if row.value.kind is not ValueKind.POINT]
    assert not any(row.identity.quantity.is_unknown for row in migrated)
    assert not any(row.identity.species.phase.is_unknown for row in migrated)

    by_species_quantity = {
        (row.identity.species.formula, row.identity.quantity.value.value): row
        for row in migrated
    }
    assert len(by_species_quantity) == 15
    assert by_species_quantity[("Na3FeO3", "delta_fG")].evidence.class_.value.value == "quoted_attributed"
    assert by_species_quantity[("Na3FeO3", "delta_fH")].evidence.class_.value.value == "model_derived"
    assert by_species_quantity[("Na3FeO3", "S")].evidence.class_.value.value == "author_estimate"
    for (species, quantity), printed_value in TABLE_VALUES.items():
        row = by_species_quantity[(species, quantity)]
        assert row.value.point == Decimal(printed_value)
        assert row.identity.temperature_K.value == Decimal("298")
        assert row.point_conditions["temperature_K"].state.value == Decimal("298")
        assert row.point_conditions["temperature_K"].locator.page == 2
        assert (row.locator.page, row.locator.table) == (2, "1")
        assert not row.identity.composition.is_value
        assert not row.identity.total_pressure_Pa.is_value
        assert not row.identity.fO2_Pa.is_value
        assert "total_pressure_Pa" not in row.point_conditions
        assert "fO2_Pa" not in row.point_conditions

    assert by_species_quantity[("NaFeO2", "delta_fG")].value.point == Decimal("-639.98")
    assert by_species_quantity[("Na4FeO3", "delta_fH")].value.point == Decimal("-1206.13")
    assert by_species_quantity[("Na3FeO3", "S")].value.point == Decimal("172.0")
