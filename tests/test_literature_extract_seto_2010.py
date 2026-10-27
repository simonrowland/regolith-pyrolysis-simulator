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
    "seto_2010_table1_r2_sls_1473k": ("RT_ln_a_Na2O_kJ_mol_as_printed", "-263.0"),
    "seto_2010_table2_r2_als_1473k": ("log10_a_Na2O_as_printed", "-7.98"),
    "seto_2010_table2_r3_als_1473k": ("RT_ln_a_Na2O_kJ_mol_as_printed", "-216.6"),
}


def _assert_page_2_pins(document: dict) -> None:
    observations = {
        row["observation_id"]: row
        for row in document["species"]["Na2O"]["observations"]
    }
    for observation_id, (field, expected) in _PINS.items():
        assert observations[observation_id]["values"][field] == expected


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
    for row in rows.values():
        assert row.identity.temperature_K.value == 1473
        # The printed composition remains in the typed-refused source rows;
        # the quantity schema cannot promote it into activity identity here.
        assert row.identity.composition.is_unknown
        assert row.point_conditions["composition"].state.is_value
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.identity.fO2_Pa.is_unknown
        assert row.value.unavailable_reason
    assert sum(row.value.kind.value == "unavailable" for row in rows.values()) == 5
    assert dict(rows["seto_2010_table1_r2_sls_1473k"].point_conditions["composition"].state.value.components) == {
        "SiO2": Decimal("0.74"), "Na2O": Decimal("0.16"), "CaO": Decimal("0.1")
    }
    assert dict(rows["seto_2010_table2_r2_als_1473k"].point_conditions["composition"].state.value.components) == {
        "SiO2": Decimal("0.65"), "Na2O": Decimal("0.3"), "Al2O3": Decimal("0.05")
    }
    assert dict(rows["seto_2010_table2_r3_als_1473k"].point_conditions["composition"].state.value.components) == {
        "SiO2": Decimal("0.6"), "Na2O": Decimal("0.3"), "Al2O3": Decimal("0.1")
    }


def test_seto_2010_source_pin_fails_if_value_is_mutated() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    document["species"]["Na2O"]["observations"][-1]["values"][
        "RT_ln_a_Na2O_kJ_mol_as_printed"
    ] = "-216.7"
    try:
        _assert_page_2_pins(document)
    except AssertionError:
        return
    raise AssertionError("mutating a render-pinned cell should fail")
