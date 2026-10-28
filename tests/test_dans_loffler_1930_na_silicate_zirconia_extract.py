"""Render pins and isolated migration checks for D’Ans and Löffler (1930)."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Phase, Quantity
from simulator.battery.migrate import (
    Migrator,
    REPO_ROOT,
    _unavailable_missing_input_work_items,
)


EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/dans-loffler-1930-na-silicate-zirconia-phase-equilibria.yaml"
)
EXPECTED_ROW_COUNTS = {
    "table_1_page_9": 12,
    "table_2_page_12": 12,
    "table_3_page_13": 18,
    "table_4_page_18": 13,
    "table_5_page_26": 2,
    "table_6_page_28": 16,
    "table_7_page_30": 2,
    "page_34_pure_compounds": 7,
    "page_34_binary_invariants": 3,
    "page_35_ternary_invariants": 6,
    "page_35_binary_boundary_invariants": 4,
}
RENDER_PINS = {
    "dans1930_p009_t01_r04_c10_experiment_1_run_135_6": (9, "1", "T_C", "1080"),
    "dans1930_p013_t03_r06_c08_point_14": (13, "3", "T_C", "959"),
    "dans1930_p028_t10_r19_c06_point_47_detectable": (28, "6", "T_C", "1446"),
    "dans1930_p035_t14_r03_c03_K": (35, None, "T_C", "1007"),
    "dans1930_p034_t13_r04_c03_O": (34, None, "T_C", "1500"),
}
COMPOSITION_PIN = "dans1930_p035_t14_r03_c04_K_Na2O_equilibrium_mol_fraction"
O_POINT_IDS = (
    "dans1930_p034_t13_r04_c03_O",
    "dans1930_p034_t13_r04_c04_O_Na2O_equilibrium_mol_fraction",
    "dans1930_p034_t13_r04_c06_O_ZrO2_equilibrium_mol_fraction",
)
APPROXIMATE_IDS = frozenset(
    {
        "dans1930_p018_t05_r14_c07_point_28_run_125_123",
        "dans1930_p018_t05_r15_c07_point_29_run_124_65",
        "dans1930_p018_t05_r16_c07_point_29_continuation_run_127_131",
        "dans1930_p026_t09_r05_c07_point_32",
        *O_POINT_IDS,
        "dans1930_p034_t12_r05_c03_Na2ZrO3",
    }
)
MODEL_DERIVED_IDS = frozenset(
    {
        "dans1930_p026_t09_r04_c08_point_31",
        "dans1930_p034_t13_r02_c03_B",
        "dans1930_p034_t13_r04_c04_O_Na2O_equilibrium_mol_fraction",
        "dans1930_p034_t13_r04_c06_O_ZrO2_equilibrium_mol_fraction",
        "dans1930_p035_t14_r02_c03_G",
        "dans1930_p035_t15_r02_c03_F",
        "dans1930_p035_t15_r02_c04_F_Na2O_equilibrium_mol_fraction",
        "dans1930_p035_t15_r02_c05_F_SiO2_equilibrium_mol_fraction",
        "dans1930_p035_t15_r02_c06_F_ZrO2_equilibrium_mol_fraction",
    }
)


def _source_observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for block in document["species"].values()
        for row in block.get("observations", [])
    }


def _assert_render_pins(document: dict) -> None:
    rows = _source_observations(document)
    for observation_id, (page, table, field, expected) in RENDER_PINS.items():
        row = rows[observation_id]
        assert row["locator"]["page"] == page
        assert row["locator"].get("table") == table
        assert str(row["values"][field]) == expected
    composition = rows[COMPOSITION_PIN]
    assert composition["locator"]["page"] == 35
    assert "table" not in composition["locator"]
    assert composition["values"]["component_amount_as_published"] == "54,0 mol%"
    assert Decimal(composition["values"]["component_amount"]) == Decimal("0.54")
    for observation_id in O_POINT_IDS:
        row = rows[observation_id]
        assert row["values"]["T_C"] == "1500"
        assert row["values"]["T_C_as_published"] == "ca. 1500"
        if observation_id != O_POINT_IDS[0]:
            assert str(row["point_conditions"]["temperature_K"]["state"]["value"]) == "1773.15"
    assert rows["dans1930_p034_t13_r04_c04_O_Na2O_equilibrium_mol_fraction"]["values"]["component_amount_as_published"] == "(57) mol%"
    assert rows["dans1930_p034_t13_r04_c06_O_ZrO2_equilibrium_mol_fraction"]["values"]["component_amount_as_published"] == "(43) mol%"

    table5_rows = [
        row
        for row in rows.values()
        if row["locator"]["page"] == 26 and row["locator"].get("table") == "5"
    ]
    assert len(table5_rows) == 5
    assert all(
        row["values"].get("composition_columns") == {"ZrO2": 3, "Na2O": 4, "SiO2": 5}
        for row in table5_rows
    )
    assert rows["dans1930_p026_t09_r05_c08_point_32"]["values"]["T_C_as_published"] == "1050"


@pytest.fixture(scope="module")
def migrated():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return document, migrator.result


def test_render_pins_row_counts_and_expected_mutation_failure(migrated) -> None:
    document, result = migrated
    _assert_render_pins(document)
    assert document["scope_assessment"]["printed_row_counts"] == EXPECTED_ROW_COUNTS
    assert len(_source_observations(document)) == 167

    mutant = deepcopy(document)
    rows = _source_observations(mutant)
    rows["dans1930_p009_t01_r04_c10_experiment_1_run_135_6"]["values"]["T_C"] = "1081"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutant)

    wrong_invariant = deepcopy(document)
    rows = _source_observations(wrong_invariant)
    rows["dans1930_p034_t13_r04_c03_O"]["values"]["T_C"] = "1600"
    with pytest.raises(AssertionError):
        _assert_render_pins(wrong_invariant)


def test_isolated_migration_has_typed_rows_and_printed_conditions(migrated) -> None:
    document, result = migrated
    rows = tuple(result.observations.values())
    assert len(rows) == 167
    assert not result.validation.hard_issues
    assert len(_unavailable_missing_input_work_items(result.observations)) == 0

    quantity_counts = {
        quantity: sum(
            row.identity.quantity.is_value and row.identity.quantity.value is token
            for row in rows
        )
        for quantity, token in (
            ("transition_temperature", Quantity.TRANSITION_TEMPERATURE),
            ("equilibrium_phase_composition", Quantity.EQUILIBRIUM_PHASE_COMPOSITION),
        )
    }
    assert quantity_counts == {
        "transition_temperature": 131,
        "equilibrium_phase_composition": 36,
    }
    assert all(row.identity.quantity.is_value for row in rows)
    assert all(row.identity.species.phase.is_value for row in rows)
    assert all(row.identity.species.phase.value in {Phase.L, Phase.CR} for row in rows)

    migrated_by_id = {row.observation_id.rsplit("::", 1)[-1]: row for row in rows}
    for observation_id in O_POINT_IDS:
        row = migrated_by_id[observation_id]
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("1773.15")
    approximate_ids = {
        observation_id
        for observation_id, row in migrated_by_id.items()
        if row.value.approximate
    }
    model_derived_ids = {
        observation_id
        for observation_id, row in migrated_by_id.items()
        if row.evidence.class_.is_value
        and row.evidence.class_.value is EvidenceClass.MODEL_DERIVED
    }
    assert approximate_ids == APPROXIMATE_IDS
    assert model_derived_ids == MODEL_DERIVED_IDS
    assert len(approximate_ids | model_derived_ids) == 15

    source_rows = _source_observations(document)
    for observation_id in APPROXIMATE_IDS:
        assert source_rows[observation_id]["values"]["approximate"] is True
        assert migrated_by_id[observation_id].value.approximate
    for observation_id in MODEL_DERIVED_IDS:
        values = source_rows[observation_id]["values"]
        assert values["method_class"] == "model_derived"
        assert "evidence_class" not in values
        derivation = values["derivation"]
        assert derivation["raw_inputs"]["state"] == {"tag": "value", "value": "not_printed"}
        assert derivation["raw_inputs"]["locator"]["page"] in {13, 26, 34, 35}
        migrated_row = migrated_by_id[observation_id]
        assert migrated_row.evidence.class_.value is EvidenceClass.MODEL_DERIVED
        assert migrated_row.derived_from is None
        assert migrated_row.derivation is not None
        assert migrated_row.derivation.method is not None
        method = migrated_row.derivation.method
        assert method.state.value == "The authors state that parenthetical values are only derived."
        assert method.locator is not None
        assert method.locator.page == 13
        assert method.locator.pdf_page_index == 12
        assert method.locator.published_page == 13
        assert method.locator.equation == "parentheses mark values as only derived"
        assert method.locator.note is not None
        assert "Einklammern der Zahlen kenntlich gemacht ist" in method.locator.note
        assert migrated_row.derivation.references == ("D’Ans and Löffler (1930), p. 13",)

    u_point_id = "dans1930_p026_t09_r05_c07_point_32"
    assert source_rows[u_point_id]["values"]["T_C_as_published"] == "U 1056"
    assert migrated_by_id[u_point_id].value.approximate

    point_31 = source_rows["dans1930_p026_t09_r04_c08_point_31"]
    assert point_31["values"]["T_C_as_published"] == "(1038)"
    point_32 = source_rows["dans1930_p026_t09_r05_c08_point_32"]
    assert point_32["values"]["T_C_as_published"] == "1050"
    assert point_32["values"]["method_class"] == "measured_direct"

    source_rows = _source_observations(document)
    for row in rows:
        conditions = row.point_conditions
        assert conditions is not None
        temperature = conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.locator is not None
        oxygen_fugacity = conditions["fO2_Pa"]
        assert oxygen_fugacity.state.is_unknown
        assert oxygen_fugacity.state.reason == "not_printed"
        assert oxygen_fugacity.locator is not None
        if row.identity.quantity.value is Quantity.TRANSITION_TEMPERATURE:
            assert "total_pressure_Pa" not in conditions
            assert row.identity.total_pressure_Pa.is_unknown
            assert row.identity.total_pressure_Pa.reason == "not_printed"
        else:
            pressure = conditions["total_pressure_Pa"]
            assert pressure.state.is_unknown
            assert pressure.state.reason == "not_printed"
            assert row.identity.total_pressure_Pa.is_unknown
        assert not row.identity.fO2_Pa.is_value

    for source in source_rows.values():
        for field in ("total_pressure_Pa", "fO2_Pa"):
            condition = source["point_conditions"][field]
            assert condition["state"]["tag"] == "unknown"
            assert condition["state"]["reason"] == "not_printed"
            assert condition["locator"]["page"] == source["locator"]["page"]
        values = source["values"]
        if "composition_mol" in values:
            condition = source["point_conditions"]["composition"]
            assert condition["state"]["tag"] == "value"
            assert condition["locator"]["page"] == source["locator"]["page"]

    transition_id = "dans1930_p009_t01_r04_c08_experiment_1_run_135_6"
    transition = next(row for row in rows if row.observation_id.endswith(f"::{transition_id}"))
    assert transition.identity.composition.is_value
    assert transition.identity.composition.value.as_map() == {
        "Na2O": Decimal("0.663"),
        "SiO2": Decimal("0.337"),
    }
    assert transition.identity.total_pressure_Pa.is_unknown
    assert transition.identity.total_pressure_Pa.reason == "not_printed"
    assert transition.identity.fO2_Pa.is_not_applicable
    assert transition.identity.temperature_K.is_not_applicable
    assert transition.point_conditions["fO2_Pa"].state.is_unknown
    assert transition.point_conditions["temperature_K"].state.value == Decimal("1348.15")

    composition_id = COMPOSITION_PIN
    composition = next(row for row in rows if row.observation_id.endswith(f"::{composition_id}"))
    assert composition.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
    assert composition.identity.species.phase.value is Phase.L
    assert composition.identity.phase_role.is_value
    assert composition.identity.phase_role.value.value == "melt"
    assert composition.identity.subtype.is_value
    assert composition.identity.subtype.value == "mole_fraction"
    assert composition.identity.composition.is_value
    assert composition.identity.composition.value.as_map() == {
        "Na2O": Decimal("0.54"),
        "SiO2": Decimal("0.45"),
        "ZrO2": Decimal("0.01"),
    }
    assert composition.value.point == Decimal("0.54")
    assert composition.identity.temperature_K.is_value
    assert composition.identity.temperature_K.value == Decimal("1280.15")
    assert composition.identity.total_pressure_Pa.is_unknown
    assert composition.point_conditions["total_pressure_Pa"].state.reason == "not_printed"
    assert composition.identity.fO2_Pa.is_unknown
    assert composition.point_conditions["fO2_Pa"].state.reason == "not_printed"

    binary_invariant_id = "dans1930_p034_t13_r04_c03_O"
    binary_invariant = next(
        row for row in rows if row.observation_id.endswith(f"::{binary_invariant_id}")
    )
    assert binary_invariant.identity.composition.is_value
    assert binary_invariant.identity.composition.value.as_map() == {
        "Na2O": Decimal("0.57"),
        "ZrO2": Decimal("0.43"),
    }
    assert binary_invariant.identity.total_pressure_Pa.is_unknown
    assert binary_invariant.identity.total_pressure_Pa.reason == "not_printed"
    assert binary_invariant.identity.fO2_Pa.is_not_applicable
    assert binary_invariant.point_conditions["temperature_K"].state.value == Decimal("1773.15")
