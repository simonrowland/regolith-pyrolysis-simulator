from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import (
    EvidenceClass,
    Phase,
    Quantity,
    ReferenceStateConvention,
)
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).parents[1]
EXTRACT = (
    ROOT / "data/literature/extracts/beerkens-2003-sulfate-decomposition-na2o-activity.yaml"
)


def test_beerkens_printed_table_values_and_deferred_figures_are_preserved() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    sulfur = extract["species"]["S"]["context"]
    table_i = next(row for row in sulfur if row["observation_id"].endswith("table_i_sulfur_saturation_context"))
    table_ii = next(row for row in sulfur if row["observation_id"].endswith("table_ii_sulfite_solubility_context"))

    assert table_i["locator"]["page"] == 3
    assert table_i["values"]["rows"][2]["float_glass_mol_m3"] == 0.635
    assert table_i["values"]["rows"][0]["model_glass_mol_m3"] == "unknown"
    assert table_ii["locator"]["page"] == 6
    assert table_ii["values"]["rows"][0]["model_glass"] == 26
    assert table_ii["values"]["rows"][4]["minimum_budd_curve_model_glass"] == 8

    figures = [row for row in sulfur if row["type"] == "figure_record"]
    assert [row["locator"]["figure"] for row in figures] == ["1", "2", "3", "4", "5", "6"]
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all("marker_count" in series for row in figures for series in row["values"]["series"])


def test_beerkens_printed_activity_point_retains_its_approximation_and_conditions() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    activity = extract["species"]["Na2O"]["context"][0]
    assert activity["values"]["value_as_published"] == "about 1.7 × 10^-8"
    assert activity["values"]["temperature_K"] == 1673
    assert activity["values"]["inputs_as_published"]["sulfur_dioxide_partial_pressure_bar"] == 0.666


def _assert_beerkens_activity_source_pins(extract: dict) -> None:
    sodium = extract["species"]["Na2O"]
    activity_context = next(
        row
        for row in sodium["context"]
        if row["observation_id"] == "beerkens_2003_sodium_oxide_activity_1673K_context"
    )
    fit_context = next(
        row
        for row in sodium["context"]
        if row["observation_id"] == "beerkens_2003_activity_and_equilibrium_fits_context"
    )
    assert fit_context["values"]["sodium_oxide_activity_model_glass"] == (
        "exp(-43552/T + 8.33)"
    )
    assert fit_context["values"]["sodium_oxide_activity_float_glass"] == (
        "exp(-46000/T + 9.50)"
    )
    assert (
        fit_context["locator"]["page"],
        fit_context["locator"]["published_page"],
        fit_context["locator"]["section"],
    ) == (5, 1897, "VI. Results for the Sodium Oxide Activity")
    assert activity_context["locator"]["section"] == "VI. Results for the Sodium Oxide Activity"
    assert activity_context["values"]["quantity"] == "activity"
    assert activity_context["values"]["method_class"] == "measured_tabulated"
    assert activity_context["values"]["estimated_error_component_1_as_published"] == (
        "about 15%–20%"
    )
    assert activity_context["values"]["estimated_error_component_2_as_published"] == "15%"

    observations = sodium["observations"]
    assert len(observations) == 1
    point = observations[0]
    assert point["observation_id"] == "beerkens_2003_float_glass_activity_at_1673K"
    assert point["type"] == "activity_coefficient"
    assert point["locator"]["page"] == 6
    assert point["locator"]["published_page"] == 1898
    assert point["locator"]["pdf_page_index"] == 5
    assert point["locator"]["section"] == "VI. Results for the Sodium Oxide Activity"
    assert point["values"]["T_K"] == "1673"
    assert point["values"]["quantity"] == "activity"
    assert point["values"]["method_class"] == "measured_tabulated"
    assert point["values"]["evidence_class"] == "measured_tabulated"
    assert point["values"]["activity"] == 1.7e-8
    assert point["values"]["approximate"] is True
    assert point["values"]["printed_activity_as_published"] == "about 1.7 × 10^-8"
    assert point["values"]["estimated_maximum_error_as_published"] == "30%–40%"
    assert point["values"]["estimated_error_component_1_as_published"] == (
        "about 15%–20%"
    )
    assert point["values"]["estimated_error_component_2_as_published"] == "15%"
    assert point["values"]["composition_mol_basis"] == (
        "mole_fraction_calculated_from_weight_percent"
    )
    assert point["point_conditions"]["pO2_Pa"]["state"] == {
        "tag": "value",
        "value": 33000,
    }
    assert point["point_conditions"]["pO2_Pa"]["locator"]["page"] == 6
    assert point["point_conditions"]["pSO2_Pa"]["state"] == {
        "tag": "value",
        "value": 66600,
    }
    assert point["point_conditions"]["pSO2_Pa"]["locator"]["page"] == 6
    composition_condition = point["point_conditions"]["composition"]
    assert composition_condition["state"]["value"]["components"] == [
        ["SiO2", "72.2"],
        ["Na2O", "13.4"],
        ["CaO", "9.5"],
        ["MgO", "2.2"],
        ["Al2O3", "1.5"],
        ["K2O", "0.9"],
    ]
    assert composition_condition["locator"]["page"] == 4
    standard_state = point["standard_state"]
    assert standard_state["state"]["value"]["convention"] == (
        "raoultian_pure_endmember"
    )
    assert standard_state["state"]["value"]["endmember"]["phase"]["tag"] == "unknown"
    assert standard_state["inferred"] is False


def test_beerkens_activity_source_pins_reject_a_mutated_fit_value() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    _assert_beerkens_activity_source_pins(extract)
    mutated = yaml.safe_load(EXTRACT.read_text())
    fit_context = next(
        row
        for row in mutated["species"]["Na2O"]["context"]
        if row["observation_id"] == "beerkens_2003_activity_and_equilibrium_fits_context"
    )
    fit_context["values"]["sodium_oxide_activity_model_glass"] = "exp(-43553/T + 8.33)"
    try:
        _assert_beerkens_activity_source_pins(mutated)
    except AssertionError:
        pass
    else:
        raise AssertionError("mutating the rendered fit coefficient did not fail the pin")


def test_beerkens_activity_point_survives_isolated_migration() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    _assert_beerkens_activity_source_pins(extract)
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()

    assert migrator.result.registry_issues == []
    assert migrator.result.validation.hard_issues == ()
    assert len(migrator.result.observations) == 1
    point = next(iter(migrator.result.observations.values()))
    assert point.observation_id.endswith(
        "::beerkens_2003_float_glass_activity_at_1673K"
    )
    assert point.identity.quantity.value is Quantity.ACTIVITY
    assert point.identity.species.phase.value is Phase.L
    assert point.value.point == Decimal("1.7E-8")
    assert point.value.approximate
    assert point.evidence.class_.value is EvidenceClass.MEASURED_TABULATED
    assert point.locator.page == 6
    assert point.locator.published_page == 1898
    assert point.identity.temperature_K.is_value
    assert point.identity.temperature_K.value == Decimal("1673")
    assert point.identity.composition.is_value
    assert dict(point.identity.composition.value.components)["Na2O"] == Decimal(
        "0.1297636248391307182437973734"
    )

    assert point.identity.reference_state.is_value
    reference_state = point.identity.reference_state.value
    assert reference_state.convention is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
    assert reference_state.endmember.formula == "Na2O"
    assert reference_state.endmember.phase.is_unknown
    assert reference_state.endmember.polymorph.is_unknown
    assert reference_state.reference_pressure_bar is None

    for observation in migrator.result.observations.values():
        assert observation.identity.temperature_K.is_value
        conditions = observation.point_conditions
        assert conditions is not None
        assert {"pO2_Pa", "pSO2_Pa", "composition"} <= set(conditions)
        assert all(condition.state.is_value for condition in conditions.values())
        assert all(condition.locator is not None for condition in conditions.values())
    assert point.point_conditions["pO2_Pa"].state.value == Decimal("33000")
    assert point.point_conditions["pO2_Pa"].locator.page == 6
    assert point.point_conditions["pSO2_Pa"].state.value == Decimal("66600")
    assert point.point_conditions["pSO2_Pa"].locator.page == 6
    assert point.point_conditions["composition"].locator.page == 4
    assert point.point_conditions["composition"].locator.published_page == 1896
