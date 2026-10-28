from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/simeonov-sano-1985-na-bearing-mno-activities.yaml"
)


def _assert_table1_pins(doc: dict) -> None:
    from simulator.battery.enums import Quantity, Rail
    from simulator.battery.score import rail_for_quantity

    rows = doc["species"]["MnO"]["observations"]
    expected = {
        "simeonov_sano_1985_t1_r2_lmn": "0.80",
        "simeonov_sano_1985_t1_r3_lmn": "0.64",
        "simeonov_sano_1985_t1_r4_lmn": "0.53",
        "simeonov_sano_1985_t1_r5_lmn": "0.41",
    }
    assert len(rows) == 4
    by_id = {row["observation_id"]: row for row in rows}
    assert set(by_id) == set(expected)
    for observation_id, value in expected.items():
        row = by_id[observation_id]
        assert row["values"]["distribution_ratio"] == value
        assert row["locator"]["page"] == 5
        assert row["locator"]["published_page"] == 1120
        assert row["locator"]["table"] == "1"
        assert row["phase"] == "l"
        assert row["values"]["formula"] == "Mn"
        assert row["values"]["phase_label"] == "slag"
        assert row["values"]["counter_phase_label"] == "metal"
        assert row["values"]["method_class"] == "measured_reduced"
        assert row["derivation"]["relation"] == "(MnO)/[Mn]"
        assert row["derivation"]["raw_inputs"]["state"]["value"] == (
            "not_printed"
        )
        assert rail_for_quantity(
            Quantity.DISTRIBUTION_RATIO, species_formula=row["values"]["formula"]
        ) is Rail.REDOX


def test_table1_mn_distribution_rows_match_render_and_migrate(tmp_path: Path) -> None:
    from tests.battery.test_migrate import _migrate_real_extract

    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = doc["species"]["MnO"]["observations"]
    expected = {
        "simeonov_sano_1985_t1_r2_lmn": "0.80",
        "simeonov_sano_1985_t1_r3_lmn": "0.64",
        "simeonov_sano_1985_t1_r4_lmn": "0.53",
        "simeonov_sano_1985_t1_r5_lmn": "0.41",
    }
    _assert_table1_pins(doc)
    by_id = {row["observation_id"]: row for row in rows}

    # Mutation proof: the independent page-5 render pins the stored cells.
    changed = dict(doc)
    changed["species"] = {"MnO": {"observations": [dict(row) for row in rows]}}
    changed["species"]["MnO"]["observations"][0] = dict(
        changed["species"]["MnO"]["observations"][0]
    )
    changed["species"]["MnO"]["observations"][0]["values"] = dict(
        changed["species"]["MnO"]["observations"][0]["values"]
    )
    changed["species"]["MnO"]["observations"][0]["values"]["distribution_ratio"] = "0.81"
    with pytest.raises(AssertionError):
        _assert_table1_pins(changed)

    result = _migrate_real_extract(tmp_path, EXTRACT.name)
    assert result.validation is not None
    assert result.validation.hard_issues == (), result.validation.hard_issues
    migrated = {
        row.observation_id.split("::", 1)[-1]: row
        for row in result.observations.values()
        if row.source_id == "simeonov-sano-1985-na-bearing-mno-activities"
    }
    assert len(migrated) == 5, (
        f"observations={len(result.observations)} contexts={sum(map(len, result.context_by_work.values()))} "
        f"queue={[entry.why for entry in result.queue]} issues={result.validation.issues}"
    )
    for observation_id, value in expected.items():
        row = migrated[observation_id]
        assert row.value.point == Decimal(value)
        assert row.identity.temperature_K.value == Decimal("1573.15")
        assert row.point_conditions["temperature_C"].state.value == Decimal("1300")
        assert row.point_conditions["gas_mix"].state.value == "CO"
        assert row.identity.composition.is_unknown
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.identity.fO2_Pa.is_unknown
    gamma = migrated["simeonov_sano_1985_gamma_mn_carbon_saturated_iron"]
    assert gamma.value.point == Decimal("0.510")
    assert gamma.identity.quantity.value.value == "activity_coefficient"
    assert gamma.identity.temperature_K.value == Decimal("1573.15")
    gamma_source = doc["species"]["Mn"]["observations"][0]
    assert gamma_source["values"]["method_class"] == "measured_reduced"
    assert gamma_source["derivation"]["parameters"][0][1]["state"]["value"] == "1.07"
    assert gamma_source["derivation"]["parameters"][1][1]["state"]["value"] == "0.544"
    assert sum(map(len, result.context_by_work.values())) == 5
    assert not any("phase string 'slag'" in entry.why for entry in result.queue)


def test_na2o_prose_conflict_and_figure_values_stay_typed_refused() -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = doc["species"]["MnO"]["context"]
    assert len(context) == 2
    assert context[0]["locator"]["figure"] == "6"
    assert context[0]["values"]["admission_status"] == "typed_refusal"
    assert context[0]["values"]["total_pressure_as_published"] == (
        "P_CO = 1 atm (Figure 6 panel annotation)"
    )
    assert "CaF2≈30 wt%" in context[0]["values"]["slag_composition_as_published"]
    assert context[1]["values"]["admission_status"] == "typed_refusal"
    assert context[1]["values"]["distribution_ratio_as_published"] == (
        "0.37 and 0.32, respectively"
    )
    assert context[1]["locator"]["page"] == 5
    assert context[1]["locator"]["published_page"] == 1120
    capacity = doc["species"]["Mn"]["context"][0]
    assert capacity["values"]["admission_status"] == "typed_refusal"
    assert capacity["values"]["defining_equation_as_printed"] == (
        "C_Mn2+ = (%Mn2+) / (a_Mn(l) · P_O2^1/2) = "
        "K / (f_Mn2+ · a_O2-) (Eq. 8)"
    )
    assert "no existing quantity token" in capacity["values"]["schema_gap"].lower()
    rows = capacity["values"]["table_rows"]
    assert len(rows) == 4
    assert [row["log_C_Mn2_plus"] for row in rows] == [
        "10.53",
        "10.52",
        "10.50",
        "10.53",
    ]
    assert capacity["values"]["equation_locator"]["page"] == 3
