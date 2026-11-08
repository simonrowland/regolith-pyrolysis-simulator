from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "greig-1927-immiscibility-part2"
EXTRACT = ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"
RENDER_PINS = {
    ("CaO", "greig1927_cao_silica_rich_cao"): ("0.6", 21, None),
    ("MgO", "greig1927_mgo_less_siliceous_mgo"): ("31", 21, None),
    ("FeO", "greig1927_table8_feo_rich_a_feo"): ("38.3", 3, "VIII"),
    ("SiO2", "greig1927_feo_cristobalite_two_liquids_transition_1689"): (
        "1689",
        21,
        None,
    ),
}


def _source_rows(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        row["observation_id"]: (formula, row)
        for formula, body in document["species"].items()
        for row in body.get("observations", [])
    }


def _assert_render_pins(document: dict) -> None:
    rows = _source_rows(document)
    assert len(rows) == 27
    assert len(document["fidelity_samples"]) == 18
    for (formula, observation_id), (expected, page, table) in RENDER_PINS.items():
        actual_formula, row = rows[observation_id]
        assert actual_formula == formula
        assert row["values"].get("component_amount", row["values"].get("value")) == expected
        locator = row["locator"]
        assert locator["page"] == page
        assert locator.get("table") == table


def test_render_transcriptions_and_row_counts_are_pinned() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_render_pins(document)

    contexts = {
        row["observation_id"]: row
        for body in document["species"].values()
        for row in body.get("context", [])
    }
    table8 = contexts["greig1927_table8_charge_and_c_analysis_context"]
    table9 = contexts["greig1927_table9_other_binary_silica_run_products"]
    table10 = contexts["greig1927_table10_b2o3_sio2_runs"]
    assert table8["values"]["table_body_row_count"] == 9
    assert table8["values"]["C_analysis_wt_percent"] == {
        "FeO": "2.94",
        "Fe2O3": ".13",
        "SiO2_by_difference": "96.9",
    }
    assert len(table9["values"]["rows"]) == 9
    assert len(table10["values"]["rows"]) == 4
    assert table10["values"]["rows"][2]["heat_treatment_steps"][1][
        "duration_hours_as_published"
    ] == "1¼"


def test_render_pin_mutation_is_detected() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    changed = copy.deepcopy(document)
    changed["species"]["CaO"]["observations"][0]["values"][
        "component_amount"
    ] = "0.7"

    with pytest.raises(AssertionError):
        _assert_render_pins(changed)


def test_isolated_migration_preserves_identity_phase_values_and_uncertainty() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(
        ROOT,
        EXTRACT,
        index={SOURCE_ID: dict(document["source"])},
        aliases={},
    )
    migrator.finalize()
    result = migrator.result

    assert result.validation is not None
    expected_unresolved_inputs = {
        "Complete-crystal-disappearance temperature in the CaO-SiO2 control mixture, 1698°.",
        "Complete-crystal-disappearance temperature in the BaO-SiO2 control mixture, 1679°.",
    }
    assert len(result.validation.hard_issues) == 4
    assert all(
        issue.reason.value == "conditional_field"
        and (
            any(raw_input in issue.detail for raw_input in expected_unresolved_inputs)
            or issue.detail.startswith("printed derivation inputs do not resolve;")
            or issue.detail == "derived observation requires derived_from"
        )
        for issue in result.validation.hard_issues
    )
    assert all(
        sum(raw_input in issue.detail for issue in result.validation.hard_issues) == 1
        for raw_input in expected_unresolved_inputs
    )
    assert len(result.queue) == 3
    assert {
        raw_input
        for raw_input in expected_unresolved_inputs
        if any(raw_input in entry.why for entry in result.queue)
    } == expected_unresolved_inputs
    assert any(
        "derived evidence class with unstated ancestry" in entry.why
        for entry in result.queue
    )
    assert len(result.observations) == 27
    migrated = {
        observation_id.removeprefix(f"{SOURCE_ID}::"): observation
        for observation_id, observation in result.observations.items()
    }
    unavailable = [
        row for row in migrated.values() if row.value.kind.value == "unavailable"
    ]
    assert unavailable == []

    source_rows = _source_rows(document)
    assert len(source_rows) == 27
    assert all(row.identity.quantity.is_value for row in migrated.values())
    assert all(row.identity.species.phase.is_value for row in migrated.values())

    for observation_id, (formula, source) in source_rows.items():
        item = migrated[observation_id]
        assert item.identity.species.formula == formula
        assert item.locator.page == source["locator"]["page"]
        source_conditions = source["point_conditions"]
        for axis in ("T_C", "composition", "total_pressure_Pa", "fO2_Pa"):
            assert axis in source_conditions, observation_id
            assert source_conditions[axis]["locator"] is not None, observation_id
        assert source_conditions["T_C"]["state"]["tag"] == "value"
        conditions = item.point_conditions or {}
        if source["values"].get("quantity") == "equilibrium_phase_composition":
            amount = source["values"]["component_amount"]
            assert item.value.kind.value == "point"
            assert item.value.point == Decimal(amount)
            assert item.identity.species.phase.value.value == "glass"
            assert item.identity.phase_role.value.value == "glass"
            assert item.identity.subtype.value == "wt_percent"
            assert item.identity.composition.is_value
            assert conditions["temperature_K"].state.is_value
            assert conditions["temperature_K"].locator is not None
            assert conditions["composition"].state.is_value
            assert conditions["composition"].locator is not None
            assert conditions["total_pressure_Pa"].state.is_unknown
            assert conditions["fO2_Pa"].state.is_unknown
            assert item.identity.total_pressure_Pa.is_unknown
            assert item.identity.fO2_Pa.is_unknown

    # Identity axes for independent printed limb rows; wt% values are retained
    # as printed and the source does not state P or fO2.
    identity_examples = {
        "greig1927_cao_silica_rich_cao": (
            "CaO", {"CaO": "0.6", "SiO2": "99.4"}, "1971.15"
        ),
        "greig1927_mgo_less_siliceous_mgo": (
            "MgO", {"MgO": "31", "SiO2": "70"}, "1968.15"
        ),
        "greig1927_sro_silica_rich_sro": (
            "SrO", {"SrO": "2.4", "SiO2": "97.6"}, "1966.15"
        ),
    }
    for observation_id, (major, printed_composition, temperature) in identity_examples.items():
        item = migrated[observation_id]
        assert item.identity.composition.value.amount_basis.value == "mole_fraction"
        assert set(dict(item.identity.composition.value.components)) == {major, "SiO2"}
        raw = source_rows[observation_id][1]
        assert raw["point_conditions"]["composition"]["state"]["value"] == {
            "amount_basis": "mass_percent",
            "components": printed_composition,
        }
        assert item.identity.temperature_K.value == Decimal(temperature)
        assert item.identity.total_pressure_Pa.is_unknown
        assert item.identity.fO2_Pa.is_unknown

    transition_rows = [
        item
        for item in migrated.values()
        if item.identity.quantity.value.value == "transition_temperature"
    ]
    assert len(transition_rows) == 5
    assert all(item.value.kind.value == "point" for item in transition_rows)
    reduced_transition = migrated["greig1927_feo_text_transition_1689_plusminus10"]
    assert reduced_transition.evidence.class_.value.value == "measured_reduced"
    assert reduced_transition.derived_from is None
    assert reduced_transition.derivation is not None
    assert reduced_transition.derivation.inputs == ()
    assert (
        reduced_transition.derivation.raw_inputs.state.value.value
        == "printed_unresolved"
    )
    assert reduced_transition.derivation.raw_inputs.locator.page == 4
    source_reduced = source_rows["greig1927_feo_text_transition_1689_plusminus10"][1]
    assert source_reduced["values"]["control_temperature_bracket_as_published"] == [
        {
            "system": "CaO-SiO2",
            "composition": "15CaO, 85SiO2",
            "temperature_C": "1698",
        },
        {
            "system": "BaO-SiO2",
            "composition": "15BaO, 85SiO2",
            "temperature_C": "1679",
        },
    ]
    printed_uncertainties = [
        item for item in migrated.values() if item.uncertainty.kind.value == "printed"
    ]
    assert len(printed_uncertainties) == 5
    assert all(
        item.uncertainty.verbatim["confidence_level"] == "not_stated"
        for item in printed_uncertainties
    )
    assert {
        item.uncertainty.verbatim["value_as_published"] for item in printed_uncertainties
    } == {"±5°", "±10°", "±15°"}

    altered = [
        source
        for _, source in source_rows.values()
        if source["values"].get("quantity") == "equilibrium_phase_composition"
        and source["values"].get("quench_status") == "asserted_altered"
    ]
    assert len(altered) == 14
    assert all(row["values"]["quench_status_locator"]["page"] in {3, 21} for row in altered)
