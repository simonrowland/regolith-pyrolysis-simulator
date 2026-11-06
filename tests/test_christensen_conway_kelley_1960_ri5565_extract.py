"""Render pins and isolated migration for the NaFeO2 rows in USBM RI 5565."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_file


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / (
    "data/literature/extracts/"
    "christensen-conway-kelley-1960-ri5565-aluminate-ferrite-thermal.yaml"
)
SOURCE_ID = "christensen-conway-kelley-1960-ri5565-aluminate-ferrite-thermal"
TYPED_REFUSALS = {
    "cck60_nafeo2_transition_enthalpy_870",
    "cck60_nafeo2_transition_enthalpy_1270",
}
RENDER_PINS = {
    ("cck60_nafeo2_table7_measured_cr_heat_content", 0): ("2170", 12, "7", "399.7"),
    ("cck60_nafeo2_table7_measured_cr_heat_content", 25): ("32800", 12, "7", "1492.8"),
    ("cck60_nafeo2_table7_melting_range_heat_content", 0): ("35400", 12, "7", "1549.1"),
    ("cck60_nafeo2_table7_measured_liquid_heat_content", 0): ("48500", 12, "7", "1628.5"),
    ("cck60_nafeo2_table8_α_heat_content", 0): ("2180", 13, "8", "400"),
    ("cck60_nafeo2_table8_β_entropy", 0): ("47.50", 13, "8", "870(β)"),
    ("cck60_nafeo2_table8_γ_heat_content", 0): ("26300", 13, "8", "1270(γ)"),
    ("cck60_nafeo2_table8_l_entropy", 0): ("72.75", 13, "8", "1620(l)"),
}
MELTING_RANGE_TEMPERATURES = {
    1549.1,
    1574.6,
    1595.9,
    1596.2,
    1596.9,
    1617.2,
    1619.2,
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for row in document["species"]["NaFeO2"]["observations"]
    }


def _assert_render_pins(document: dict) -> None:
    rows = _observations(document)
    for (observation_id, index), (expected, page, table, printed_row) in RENDER_PINS.items():
        point = rows[observation_id]["values"]["points"][index]
        assert point["value"] == expected
        assert point["locator"]["page"] == page
        assert point["locator"]["table"] == table
        assert point["locator"]["row"] == printed_row


def _assert_printed_row_counts(document: dict) -> None:
    rows = _observations(document)
    table7 = sum(
        len(rows[oid]["values"]["points"])
        for oid in (
            "cck60_nafeo2_table7_measured_cr_heat_content",
            "cck60_nafeo2_table7_melting_range_heat_content",
            "cck60_nafeo2_table7_measured_liquid_heat_content",
        )
    )
    table8_heat = sum(
        len(row["values"]["points"])
        for oid, row in rows.items()
        if oid.startswith("cck60_nafeo2_table8_") and oid.endswith("_heat_content")
    )
    table8_entropy = sum(
        len(row["values"]["points"])
        for oid, row in rows.items()
        if oid.startswith("cck60_nafeo2_table8_") and oid.endswith("_entropy")
    )
    assert table7 == 38
    assert table8_heat == 23
    assert table8_entropy == 23


def _assert_reviewed_semantic_pins(document: dict) -> None:
    rows = _observations(document)
    melting = rows["cck60_nafeo2_table7_melting_range_heat_content"]
    assert melting["phase"] == "in melting range"
    assert {
        float(point["T_K"])
        for point in melting["values"]["points"]
        if point["locator"].get("note") == "Table 7 footnote: in melting range."
    } == MELTING_RANGE_TEMPERATURES

    pretransition = next(
        point
        for point in rows["cck60_nafeo2_table7_measured_cr_heat_content"]["values"]["points"]
        if point["T_K"] == "1268.8"
    )
    assert pretransition["value"] == "26110"
    assert pretransition["locator"]["note"] == "Table 7 footnote 1: shows pretransition effect."

    for greek, token in (("α", "alpha"), ("β", "beta"), ("γ", "gamma")):
        for prop in ("heat_content", "entropy"):
            row = rows[f"cck60_nafeo2_table8_{greek}_{prop}"]
            assert row["condensed_form"]["polymorph"] == token

    beta_1270 = next(
        point
        for point in rows["cck60_nafeo2_table8_β_heat_content"]["values"]["points"]
        if point["locator"]["row"] == "1270(β)"
    )
    gamma_1270 = next(
        point
        for point in rows["cck60_nafeo2_table8_γ_heat_content"]["values"]["points"]
        if point["locator"]["row"] == "1270(γ)"
    )
    assert (beta_1270["value"], gamma_1270["value"]) == ("25780", "26300")
    assert rows["cck60_nafeo2_table8_α_heat_content"]["derived_from"] == [
        "cck60_nafeo2_table7_measured_cr_heat_content"
    ]

    king = rows["cck60_nafeo2_king_standard_entropy_29815"]
    king_point = king["values"]["points"][0]
    assert king_point["value"] == "21.1"
    assert king_point["locator"]["row"] == "discussion after Figure 2, footnote 17"
    assert "table" not in king_point["locator"]
    assert king["values"]["attribution"] == "King, reference 17 (p. 3, as cited here)"
    assert king["uncertainty"] == {
        "reported_status": "printed",
        "value": "0.2",
        "units": "cal/mol/K",
        "confidence_level": "not_stated",
    }


def test_render_cells_and_printed_row_counts_are_pinned() -> None:
    document = _document()
    _assert_render_pins(document)
    _assert_printed_row_counts(document)
    _assert_reviewed_semantic_pins(document)

    rows = _observations(document)
    assert rows["cck60_nafeo2_transition_enthalpy_870"]["values"]["value"] == "0"
    assert rows["cck60_nafeo2_transition_enthalpy_1270"]["values"]["value"] == "520"
    assert rows["cck60_nafeo2_heat_of_fusion_1620"]["values"]["value"] == "11760"
    assert rows["cck60_nafeo2_entropy_of_fusion_1620"]["values"]["value"] == "7.26"
    equations = {
        row["observation_id"]: row
        for row in document["species"]["NaFeO2"]["context"]
        if row["observation_id"].endswith("printed_thermochemical_equations")
    }
    assert equations["cck60_nafeo2_α_printed_thermochemical_equations"]["values"][
        "enthalpy_equation_as_printed"
    ].endswith("ΔH₈₇₀ = 0.")
    assert equations["cck60_nafeo2_β_printed_thermochemical_equations"]["values"][
        "enthalpy_equation_as_printed"
    ].endswith("520.")
    assert equations["cck60_nafeo2_γ_printed_thermochemical_equations"]["values"][
        "enthalpy_equation_as_printed"
    ].endswith("ΔH₁₆₂₀ = 11,760.")
    assert equations["cck60_nafeo2_l_printed_thermochemical_equations"]["values"][
        "heat_capacity_equation_as_printed"
    ] == "Cₚ = 37.30"


def test_render_value_and_printed_count_mutations_fail() -> None:
    document = _document()
    mutated_value = deepcopy(document)
    rows = _observations(mutated_value)
    rows["cck60_nafeo2_table7_measured_cr_heat_content"]["values"]["points"][0][
        "value"
    ] = "2171"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated_value)

    mutated_count = deepcopy(document)
    rows = _observations(mutated_count)
    rows["cck60_nafeo2_table7_measured_cr_heat_content"]["values"]["points"].pop()
    with pytest.raises(AssertionError):
        _assert_printed_row_counts(mutated_count)

    mutated_phase = deepcopy(document)
    rows = _observations(mutated_phase)
    rows["cck60_nafeo2_table7_melting_range_heat_content"]["phase"] = "cr"
    with pytest.raises(AssertionError):
        _assert_reviewed_semantic_pins(mutated_phase)

    mutated_polymorph = deepcopy(document)
    rows = _observations(mutated_polymorph)
    rows["cck60_nafeo2_table8_β_heat_content"]["condensed_form"]["polymorph"] = "alpha"
    with pytest.raises(AssertionError):
        _assert_reviewed_semantic_pins(mutated_polymorph)


def test_validator_and_isolated_migration() -> None:
    document = _document()
    _assert_render_pins(document)
    _assert_printed_row_counts(document)
    assert validate_extract_file(EXTRACT, check_fidelity_match=True) == []

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert len(migrator.result.observations) == 92
    assert migrator.result.validation.hard_issues == ()

    unknown_quantities = {
        observation_id.rsplit("::", 1)[-1]
        for observation_id, observation in migrator.result.observations.items()
        if not observation.identity.quantity.is_value
    }
    assert unknown_quantities == TYPED_REFUSALS
    unknown_phases = {
        observation.observation_id
        for observation in migrator.result.observations.values()
        if not observation.identity.species.phase.is_value
    }
    melting_range_rows = [
        observation
        for observation in migrator.result.observations.values()
        if "cck60_nafeo2_table7_melting_range_heat_content" in observation.observation_id
    ]
    assert len(melting_range_rows) == 7
    assert unknown_phases == {row.observation_id for row in melting_range_rows}
    assert {
        float(row.identity.temperature_K.value)
        for row in melting_range_rows
    } == MELTING_RANGE_TEMPERATURES

    beta_1270 = next(
        observation
        for observation in migrator.result.observations.values()
        if "cck60_nafeo2_table8_β_heat_content" in observation.observation_id
        and observation.identity.temperature_K.is_value
        and observation.identity.temperature_K.value == 1270
    )
    gamma_1270 = next(
        observation
        for observation in migrator.result.observations.values()
        if "cck60_nafeo2_table8_γ_heat_content" in observation.observation_id
        and observation.identity.temperature_K.is_value
        and observation.identity.temperature_K.value == 1270
    )
    assert beta_1270.identity.species.polymorph.is_value
    assert gamma_1270.identity.species.polymorph.is_value
    assert beta_1270.identity.species.polymorph.value.value == "beta"
    assert gamma_1270.identity.species.polymorph.value.value == "gamma"
    assert beta_1270.identity.species.polymorph != gamma_1270.identity.species.polymorph
    assert str(beta_1270.value.point) == "107.86352"
    assert str(gamma_1270.value.point) == "110.0392"

    for observation in migrator.result.observations.values():
        temperature = observation.identity.temperature_K
        temperature_is_bound = temperature is not None and temperature.is_value
        if not temperature_is_bound:
            condition = observation.point_conditions.get("temperature_K")
            temperature_is_bound = condition is not None and condition.state.is_value
        assert temperature_is_bound
        standard_pressure = observation.identity.standard_pressure_Pa
        total_pressure = observation.identity.total_pressure_Pa
        assert standard_pressure is None or not standard_pressure.is_value
        assert total_pressure is None or not total_pressure.is_value

    # The paper does not print a pressure; the mandatory profile axis stays unknown.
    assert len(migrator.result.validation.issues) == 88
    assert {
        issue.detail for issue in migrator.result.validation.issues
    } == {
        "required axis standard_pressure_Pa is unknown",
        "required axis total_pressure_Pa is unknown",
    }
    for observation_id in (
        "cck60_nafeo2_table7_measured_cr_heat_content",
        "cck60_nafeo2_table8_β_heat_content",
    ):
        row = next(
            observation
            for key, observation in migrator.result.observations.items()
            if observation_id in key
        )
        assert row.identity.temperature_K.is_value
        assert row.identity.standard_pressure_Pa.is_unknown
    transition = next(
        observation
        for key, observation in migrator.result.observations.items()
        if "cck60_nafeo2_transition_1270" in key
    )
    assert transition.point_conditions["temperature_K"].state.is_value
    assert transition.identity.total_pressure_Pa.is_unknown

    king = next(
        observation
        for observation in migrator.result.observations.values()
        if "cck60_nafeo2_king_standard_entropy_29815" in observation.observation_id
    )
    assert king.uncertainty.kind.value == "printed"
    assert king.uncertainty.verbatim["value"] == "0.2"
    assert king.uncertainty.verbatim["units"] == "cal/mol/K"
