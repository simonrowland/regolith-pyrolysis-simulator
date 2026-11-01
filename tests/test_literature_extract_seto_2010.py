"""Source pins and isolated migration for the Seto et al. 2010 extract."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import migrate
from tests.literature_extract_test_utils import isolated_root


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/seto-2010-na2o-activity-glass-emf-summary.yaml"
SID = "seto-2010-na2o-activity-glass-emf-summary"
_PINS = {
    "seto_2010_table1_r1_sls_1473k": (
        "RT_ln_a_Na2O_kJ_mol_as_printed", "-263.9"
    ),
    "seto_2010_table1_r2_sls_1473k": ("RT_ln_a_Na2O_kJ_mol_as_printed", "-263.0"),
    "seto_2010_table2_r2_als_1473k": ("log10_a_Na2O_as_printed", "-7.98"),
    "seto_2010_table2_r3_als_1473k": ("RT_ln_a_Na2O_kJ_mol_as_printed", "-216.6"),
}
# Printed Table 1 and Table 2 mole percents, as mole fractions.
_PRINTED_MOLE_FRACTIONS = {
    "seto_2010_table1_r1_sls_1473k": {
        "SiO2": Decimal("0.84"), "Na2O": Decimal("0.16"), "CaO": Decimal("0"),
    },
    "seto_2010_table1_r2_sls_1473k": {
        "SiO2": Decimal("0.74"), "Na2O": Decimal("0.16"), "CaO": Decimal("0.1"),
    },
    "seto_2010_table2_r1_als_1473k": {
        "SiO2": Decimal("0.70"), "Na2O": Decimal("0.30"), "Al2O3": Decimal("0"),
    },
    "seto_2010_table2_r2_als_1473k": {
        "SiO2": Decimal("0.65"), "Na2O": Decimal("0.3"), "Al2O3": Decimal("0.05"),
    },
    "seto_2010_table2_r3_als_1473k": {
        "SiO2": Decimal("0.6"), "Na2O": Decimal("0.3"), "Al2O3": Decimal("0.1"),
    },
}
_TABLE_1_ROWS = {
    "seto_2010_table1_r1_sls_1473k": {
        "composition_as_printed": "84SiO2-16Na2O-0CaO (mol%)",
        "composition_components": ["84", "16", "0"],
        "temperature_K": "1473",
        "RT_ln_a_Na2O_kJ_mol_as_printed": "-263.9",
        "log10_a_Na2O_as_printed": "-9.36",
        "reference": "[2]",
    },
    "seto_2010_table1_r2_sls_1473k": {
        "composition_as_printed": "74SiO2-16Na2O-10CaO (mol%)",
        "composition_components": ["74", "16", "10"],
        "temperature_K": "1473",
        "RT_ln_a_Na2O_kJ_mol_as_printed": "-263.0",
        "log10_a_Na2O_as_printed": "-9.33",
        "reference": "This study",
    },
}


def _assert_page_2_pins(document: dict) -> None:
    observations = {
        row["observation_id"]: row
        for row in document["species"]["Na2O"]["observations"]
    }
    for observation_id, (field, expected) in _PINS.items():
        assert observations[observation_id]["values"][field] == expected
    for observation_id, expected in _TABLE_1_ROWS.items():
        row = observations[observation_id]
        assert row["T_K"] == expected["temperature_K"]
        assert row["regime"] == "emf_cell"
        assert "composition: mol%" in row["units"]
        assert row["values"]["composition_as_printed"] == expected["composition_as_printed"]
        composition = row["point_conditions"]["composition"]["state"]["value"]
        assert composition["basis"] == "printed_mole_percent"
        assert composition["amount_basis"] == "mole_percent"
        assert composition["components"] == [
            ["SiO2", expected["composition_components"][0]],
            ["Na2O", expected["composition_components"][1]],
            ["CaO", expected["composition_components"][2]],
        ]
        assert row["values"]["RT_ln_a_Na2O_kJ_mol_as_printed"] == expected[
            "RT_ln_a_Na2O_kJ_mol_as_printed"
        ]
        assert row["values"]["log10_a_Na2O_as_printed"] == expected[
            "log10_a_Na2O_as_printed"
        ]
        assert row["values"]["attribution"] == expected["reference"]


def test_seto_2010_table_cells_and_row_count_match_page_2_render() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_page_2_pins(document)

    rows = document["species"]["Na2O"]["observations"]
    assert len(rows) == 5
    assert rows[-1]["values"]["composition_as_printed"] == "60SiO2-30Na2O-10Al2O3 (mol%)"
    assert all(row["point_conditions"]["composition"]["state"]["tag"] == "value" for row in rows)


def test_seto_2010_migrates_with_printed_temperature_and_composition_only(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    result = migrate(isolated_root(tmp_path, document), write=False)
    assert result.validation is not None
    assert not result.validation.hard_issues
    rows = {
        row.observation_id.rsplit("::", 1)[1]: row
        for row in result.observations.values()
        if row.observation_id.startswith(f"{SID}::seto_2010_")
    }

    assert len(rows) == 5
    assert len(result.queue) == 10
    assert {axis for entry in result.queue for axis in entry.axes} == {
        "reference_state", "value"
    }
    for row_id, row in rows.items():
        printed = _PRINTED_MOLE_FRACTIONS[row_id]
        assert row.identity.temperature_K.value == 1473
        # b-1052 binds the explicit point-condition composition as identity.
        assert row.identity.composition.is_value
        assert dict(row.identity.composition.value.components) == printed
        assert row.point_conditions["composition"].state.is_value
        assert dict(row.point_conditions["composition"].state.value.components) == printed
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.identity.fO2_Pa.is_unknown
        assert row.value.unavailable_reason
        assert row.admission.status.value == "rejected"
    assert sum(row.value.kind.value == "unavailable" for row in rows.values()) == 5
    assert sum(row.value.kind.value != "unavailable" for row in rows.values()) == 0


def test_seto_2010_source_pin_fails_if_value_is_mutated() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    document["species"]["Na2O"]["observations"][0]["values"][
        "RT_ln_a_Na2O_kJ_mol_as_printed"
    ] = "-264.0"
    try:
        _assert_page_2_pins(document)
    except AssertionError:
        return
    raise AssertionError("mutating a render-pinned cell should fail")
