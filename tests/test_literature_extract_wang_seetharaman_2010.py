from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import migrate
from tests.helpers.corpus_paths import regolith_corpus_root
from tests.literature_extract_test_utils import isolated_root


ROOT = Path(__file__).resolve().parents[1]
SID = "wang-seetharaman-2010-cr-oxidation-original"
EXTRACT = ROOT / "data/literature/extracts" / f"{SID}.yaml"
RENDER_PINS = {
    "wang2010_table3_08d_xcro_over_xcro15_xanes": "1.38",
    "wang2010_table3_10c_xcro_over_xcro15_xanes": "9.00",
    "wang2010_table3_21d_xcro_over_xcro15_xanes": "1.78",
    "wang2010_table4_sample_1_cro_partial_pressure": "3.1e-2",
    "wang2010_table4_sample_1_cro2_partial_pressure": "6.4e-3",
}
EQ7_RATIO_PINS = {
    "wang2010_table4_sample_1_xcro_over_xcro15_eq7": "1.42",
    "wang2010_table4_sample_2_xcro_over_xcro15_eq7": "3.75",
    "wang2010_table4_sample_3_xcro_over_xcro15_eq7": "3.64",
    "wang2010_table4_sample_4_xcro_over_xcro15_eq7": "1.64",
    "wang2010_table4_sample_5_xcro_over_xcro15_eq7": "4.71",
    "wang2010_table4_sample_6_xcro_over_xcro15_eq7": "1.02",
    "wang2010_table4_sample_7_xcro_over_xcro15_eq7": "0.882",
    "wang2010_table4_sample_8_xcro_over_xcro15_eq7": "1.85",
    "wang2010_table4_sample_9_xcro_over_xcro15_eq7": "2.14",
    "wang2010_table4_sample_10_xcro_over_xcro15_eq7": "2.56",
}
TABLE_IV_FO2_NOTE = (
    "Table IV oxygen pressure calculated using Eq. (10) and accounting for "
    "tungsten-crucible interactions; not a separately imposed or measured fO2."
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _source_rows(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for payload in document["species"].values()
        for row in payload.get("observations", [])
    }


def _assert_render_pins(document: dict, pins: dict[str, str] = RENDER_PINS) -> None:
    rows = _source_rows(document)
    for observation_id, expected in pins.items():
        values = rows[observation_id]["values"]
        actual = values.get("pressure_Pa", values.get("value"))
        assert actual == expected, f"{observation_id}: expected rendered {expected}, got {actual}"


def test_printed_row_counts_and_render_pins_are_independent() -> None:
    document = _document()
    rows = _source_rows(document)
    table_iii = [row for row in rows.values() if row["locator"].get("table") == "III"]
    table_iv = [row for row in rows.values() if row["locator"].get("table") == "IV"]
    assert len(table_iii) == 9
    assert len(table_iv) == 20  # 10 each CrO and CrO2 pressure rows
    assert sum(row["locator"].get("equation") == "7" for row in rows.values()) == 10
    assert len(rows) == 39

    _assert_render_pins(document)
    for observation_id, expected in EQ7_RATIO_PINS.items():
        assert _source_rows(document)[observation_id]["values"]["value"] == expected
    assert _source_rows(document)[
        "wang2010_table4_sample_1_xcro_over_xcro15_eq7"
    ]["point_conditions"]["fO2_Pa"]["locator"]["note"] == TABLE_IV_FO2_NOTE
    assert document["fidelity_samples"][0]["locator"]["page"] == 5
    assert document["fidelity_samples"][0]["locator"]["table"] == "III"
    assert document["fidelity_samples"][0]["locator"]["table_row_number"] == 3
    assert document["fidelity_samples"][0]["locator"]["table_column_number"] == 5
    assert document["fidelity_samples"][-1]["locator"]["page"] == 5
    assert document["fidelity_samples"][-1]["locator"]["table"] == "IV"

    changed_value = copy.deepcopy(document)
    _source_rows(changed_value)[
        "wang2010_table3_10c_xcro_over_xcro15_xanes"
    ]["values"]["value"] = "9.01"
    with pytest.raises(AssertionError):
        _assert_render_pins(changed_value)

    changed_render_pin = dict(RENDER_PINS, wang2010_table3_10c_xcro_over_xcro15_xanes="9.01")
    with pytest.raises(AssertionError):
        _assert_render_pins(document, changed_render_pin)


def test_isolated_migration_binds_redox_axes_and_exact_parents(tmp_path: Path) -> None:
    document = _document()
    corpus = regolith_corpus_root()
    assert (corpus / f"raw/{SID}/{SID}.pdf").is_file()

    result = migrate(isolated_root(tmp_path, document), write=False)
    assert len(result.observations) == 39
    assert not result.validation.hard_issues

    migrated = {
        observation_id.rsplit("::", 1)[-1]: observation
        for observation_id, observation in result.observations.items()
    }
    unavailable = sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated.values())
    assert unavailable == 0
    assert all(row.identity.quantity.is_value for row in migrated.values())
    assert all(
        row.identity.species is not None and row.identity.species.phase.is_value
        for row in migrated.values()
    )
    for row in migrated.values():
        assert row.identity.temperature_K is not None and row.identity.temperature_K.is_value
        assert row.identity.fO2_Pa is not None and row.identity.fO2_Pa.is_value
        assert row.point_conditions["temperature_K"].state.is_value
        assert row.point_conditions["fO2_Pa"].state.is_value

    source_rows = _source_rows(document)
    table3_source = source_rows["wang2010_table3_10c_xcro_over_xcro15_xanes"]
    assert (table3_source["locator"]["page"], table3_source["locator"]["table"]) == (5, "III")
    assert table3_source["locator"]["table_row_number"] == 5
    assert table3_source["locator"]["table_column_number"] == 5
    glass_axes = migrated["wang2010_table3_10c_xcro_over_xcro15_xanes"].point_conditions
    assert (glass_axes["temperature_K"].locator.page, glass_axes["temperature_K"].locator.table) == (5, "III")
    assert (glass_axes["fO2_Pa"].locator.page, glass_axes["fO2_Pa"].locator.table) == (5, "III")

    table4_source = source_rows["wang2010_table4_sample_1_cro_partial_pressure"]
    assert (table4_source["locator"]["page"], table4_source["locator"]["table"]) == (5, "IV")
    assert table4_source["locator"]["table_row_number"] == 3
    assert table4_source["locator"]["table_column_number"] == 2
    table4_pressure = migrated["wang2010_table4_sample_1_cro_partial_pressure"]
    assert (table4_pressure.locator.page, table4_pressure.locator.table) == (5, "IV")
    assert table4_pressure.identity.species.phase.value == "g"
    assert (table4_pressure.point_conditions["fO2_Pa"].locator.page,
            table4_pressure.point_conditions["fO2_Pa"].locator.table) == (5, "IV")
    table4_ratio = migrated["wang2010_table4_sample_1_xcro_over_xcro15_eq7"]
    assert (table4_ratio.locator.page, table4_ratio.locator.equation) == (5, "7")
    assert table4_ratio.point_conditions["temperature_K"].locator.page == 5
    assert table4_ratio.point_conditions["temperature_K"].state.value == Decimal("2000")
    assert table4_ratio.point_conditions["fO2_Pa"].locator.note == TABLE_IV_FO2_NOTE

    for observation_id in (
        "wang2010_table3_08d_xcro_over_xcro15_xanes",
        "wang2010_table3_10c_xcro_over_xcro15_xanes",
        "wang2010_table3_21d_xcro_over_xcro15_xanes",
        "wang2010_table4_sample_1_xcro_over_xcro15_eq7",
        "wang2010_table4_sample_1_cro_partial_pressure",
    ):
        row = migrated[observation_id]
        assert row.identity.temperature_K is not None
        assert row.identity.temperature_K.is_value
        assert row.identity.composition is not None and row.identity.composition.is_unknown
        assert "per-run composition is not published" in row.identity.composition.reason
        assert row.identity.fO2_Pa is not None and row.identity.fO2_Pa.is_value
        assert row.identity.total_pressure_Pa is None or not row.identity.total_pressure_Pa.is_value
        assert row.point_conditions["temperature_K"].state.is_value
        assert row.point_conditions["composition"].state.is_unknown
        assert row.point_conditions["fO2_Pa"].state.is_value
        assert "total_pressure_Pa" not in row.point_conditions

    glass = migrated["wang2010_table3_08d_xcro_over_xcro15_xanes"]
    assert glass.identity.species.phase.value == "glass"
    assert glass.point_conditions["quench_state"].state.value == "not_stated"
    printed_glass = _source_rows(document)[
        "wang2010_table3_08d_xcro_over_xcro15_xanes"
    ]
    assert printed_glass["quench_statement"]["preservation"] == "not_stated"
    assert printed_glass["quench_statement"]["quench_direction"] == "not_stated"
    starting_mix = printed_glass["point_conditions"]["composition"]["state"]["value"]
    assert starting_mix["proxy_flag"] == "initial_charge_only"
    assert dict(starting_mix["components"])["Cr2O3"] == "10"
    assert glass.identity.temperature_K.value == Decimal("1873")
    assert glass.identity.fO2_Pa.value == Decimal("2.83e-3")

    kems_ratio = migrated["wang2010_table4_sample_1_xcro_over_xcro15_eq7"]
    assert kems_ratio.identity.species.phase.value == "l"
    for observation_id, expected in EQ7_RATIO_PINS.items():
        assert migrated[observation_id].value.point == Decimal(expected)
    assert set(kems_ratio.derived_from) == {
        f"{SID}::wang2010_table4_sample_1_cro_partial_pressure",
        f"{SID}::wang2010_table4_sample_1_cro2_partial_pressure",
    }
    assert kems_ratio.identity.composition.is_unknown
    starting_kems_mix = _source_rows(document)[
        "wang2010_table4_sample_1_xcro_over_xcro15_eq7"
    ]["point_conditions"]["composition"]["state"]["value"]
    assert starting_kems_mix["proxy_flag"] == "initial_charge_only"
    assert dict(starting_kems_mix["components"])["Fe_nO"] == "0.072"

    cr_o_pressure = migrated["wang2010_table4_sample_1_cro_partial_pressure"]
    cr_o2_pressure = migrated["wang2010_table4_sample_1_cro2_partial_pressure"]
    assert cr_o_pressure.value.point == Decimal("3.1e-2")
    assert cr_o2_pressure.value.point == Decimal("6.4e-3")
    assert all("total_pressure_Pa" not in row.point_conditions for row in migrated.values())
