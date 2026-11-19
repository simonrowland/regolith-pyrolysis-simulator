"""Regression pin for O'Neill & Eggins (2002) printed FeO uncertainties."""

from collections import Counter
import copy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import re

import pytest
import yaml

from simulator.battery.enums import (
    AdmissionStatus,
    Phase,
    Quantity,
    ReferenceStateConvention,
    UncertaintyKind,
)
from simulator.battery.score import _printed_uncertainty_band
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/oneill-2002-feo-activity-coefficients-cmas.yaml"

# Table 5, p. 162, "Number of data" by melt.
TABLE5_MO_COUNTS = {
    "AD eutectic": 6,
    "AD+Fo": 6,
    "AD+En": 7,
    "AD+Wo": 7,
    "AD+Qz": 7,
    "AD+TiO2": 7,
    "A": 9,
    "B": 9,
    "C": 9,
    "D": 9,
    "E": 9,
    "F": 9,
    "MAS1": 3,
    "MAS2": 6,
    "CAS1": 5,
    "CAS2": 6,
    "CAS4": 5,
}

TABLE7_MOO3_PRINTED = {
    "AD eutectic": "0.303 (25)",
    "AD+Fo": "0.290 (33)",
    "AD+En": "0.433 (38)",
    "AD+Wo": "0.094 (7)",
    "AD+Qz": "0.793 (78)",
    "AD+TiO2": "0.325 (36)",
    "A": "0.692 (51)",
    "B": "0.814 (61)",
    "C": "0.463 (32)",
    "D": "0.569 (39)",
    "E": "0.240 (15)",
    "F": "0.496 (35)",
    "MAS1": "1.37 (24)",
    "MAS2": "1.64 (23)",
    "CAS1": "0.079 (6)",
    "CAS2": "0.027 (2)",
    "CAS4": "0.201 (18)",
}


def _assert_table7_state_source_pins(document):
    for formula in ("NiO", "CoO", "MoO3"):
        observation = document["species"][formula]["observations"][0]
        standard_state = observation["standard_state"]
        state = standard_state["state"]
        assert state["tag"] == "value"
        value = state["value"]
        assert value["convention"] == "raoultian_pure_endmember"
        assert value["endmember"]["formula"] == formula
        assert value["endmember"]["phase"] == {"tag": "value", "value": "l"}
        assert value["component_basis"] == formula
        assert value["reference_pressure_bar"] == "1"
        assert standard_state["inferred"] is False
        assert standard_state["locator"]["page"] == 13
        assert standard_state["locator"]["published_page"] == 163
        assert standard_state["locator"]["table"] == "6"

    moo3 = document["species"]["MoO3"]["observations"][0]
    assert {
        row["melt"]: row["gamma_as_printed"] for row in moo3["values"]["series"]
    } == TABLE7_MOO3_PRINTED
    assert all(
        row["admission_status"] == "admitted" for row in moo3["values"]["series"]
    )

    moo2 = document["species"]["MoO2"]["observations"][0]
    assert moo2["standard_state"]["state"]["tag"] == "unknown"
    assert moo2["values"]["admission_status"] == "typed_refusal"
    assert "hypothetical" in moo2["values"]["reason"]


def test_feo_table_7_rows_keep_their_own_printed_one_sigma():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    source_observation = document["species"]["FeO"]["observations"][0]
    source_rows = source_observation["values"]["series"]
    composition_basis_note = source_observation["values"]["composition_basis_note"]
    assert "Table 4" in composition_basis_note
    assert "wt.% is inferred" in composition_basis_note
    source_uncertainty = source_observation["uncertainty"]
    assert source_uncertainty["note"].startswith(
        "Errors are one standard deviation and do not include systematic errors, "
        "e.g., in the standard state thermodynamic data."
    )
    assert source_uncertainty["locator"]["page"] == 13
    assert source_uncertainty["locator"]["published_page"] == 163
    assert source_uncertainty["locator"]["table"] == "7"

    migrator = migrate_one_extract(ROOT, EXTRACT, index=None, aliases={})
    migrator.finalize()
    observations = [
        observation
        for observation in migrator.result.observations.values()
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.ACTIVITY_COEFFICIENT
        and observation.identity.species.formula == "FeO"
    ]

    assert len(source_rows) == 19
    assert len(observations) == 19
    bands = []
    for row, observation in zip(source_rows, observations, strict=True):
        printed = row["gamma_as_printed"]
        match = re.fullmatch(r"\s*\d+(?:\.(\d+))?\s+\((\d+)\)\s*", printed)
        assert match is not None
        digits = len(match.group(1) or "")
        expected_sigma = Decimal(match.group(2)).scaleb(-digits)
        assert observation.uncertainty.kind is UncertaintyKind.PRINTED
        assert observation.uncertainty.verbatim["sigma"] == row["sigma"]
        assert Decimal(observation.uncertainty.verbatim["sigma"]) == expected_sigma
        assert observation.uncertainty.verbatim["confidence_level"] == "one_sigma"
        assert observation.identity.temperature_K.value == Decimal("1673.15")
        assert (
            observation.point_conditions["temperature_K"].state.value
            == Decimal("1673.15")
        )
        assert observation.point_conditions["temperature_K"].locator is not None
        assert observation.point_conditions["composition"].state.is_value
        assert observation.point_conditions["composition"].locator is not None
        resolution = _printed_uncertainty_band(
            observation.identity.quantity.value,
            observation.uncertainty,
            observation.value.point,
            source_observation=observation,
        )
        assert resolution.refusal is None
        assert resolution.band is not None
        assert resolution.band.rule.startswith("printed_1sigma:")
        bands.append(resolution.band.value)

        old_units_observation = replace(
            observation,
            derivation=replace(
                observation.derivation,
                output_unit=(
                    "dimensionless activity coefficient; "
                    "Table 4 composition values interpreted as wt.%"
                ),
            ),
        )
        old_units = _printed_uncertainty_band(
            old_units_observation.identity.quantity.value,
            old_units_observation.uncertainty,
            old_units_observation.value.point,
            source_observation=old_units_observation,
        )
        assert old_units.band is None
        assert old_units.refusal["reason"] == "printed_uncertainty_unit_unmapped"

    assert len(bands) == 19
    assert bands[:3] == [
        Decimal("0.006877170700136778177155847580"),
        Decimal("0.01462079132824429539788449331"),
        Decimal("0.007325359695819540850617842192"),
    ]


def test_locator_pages_are_physical_pdf_pages_for_printed_folios():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    locators = []

    def collect(value):
        if isinstance(value, dict):
            if {"page", "published_page", "pdf_page_index"} <= value.keys():
                locators.append(value)
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(document)
    expected_pages = {156: 6, 157: 7, 159: 9, 160: 10, 161: 11}
    changed_locators = [
        locator
        for locator in locators
        if locator["published_page"] in expected_pages
    ]
    assert Counter(locator["published_page"] for locator in changed_locators) == {
        156: 230,
        157: 211,
        159: 270,
        160: 152,
        161: 248,
    }
    for locator in changed_locators:
        page = expected_pages[locator["published_page"]]
        assert locator["page"] == page
        assert locator["pdf_page_index"] == page - 1


def test_phase_parent_pressure_inference_survives_in_observation_provenance():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    source_parents = [
        row
        for species in document["species"].values()
        for row in species["observations"]
        if row.get("type") == "equilibrium_phase_composition"
    ]
    assert len(source_parents) == 230
    pressure_source = source_parents[0]["provenance"]["identity"]["total_pressure_Pa"]
    assert pressure_source["state"] == {"tag": "value", "value": "101325"}
    assert pressure_source["inference"]["relation"] == "extract_inference"
    assert "inferred=true" in pressure_source["inference"]["inputs"]

    migrator = migrate_one_extract(ROOT, EXTRACT, index=None, aliases={})
    migrator.finalize()
    parents = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == document["source_id"]
        and observation.identity.quantity.is_value
        and observation.identity.quantity.value
        is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
    ]
    assert len(parents) == 230
    for observation in parents:
        assert observation.identity.total_pressure_Pa.is_unknown
        assert "inferred" in observation.identity.total_pressure_Pa.reason
        point_pressure = observation.point_conditions["total_pressure_Pa"]
        assert point_pressure.state.value == Decimal("101325")
        assert point_pressure.inference is not None
        assert point_pressure.inference.relation == "extract_inference"
        assert "inferred=true" in point_pressure.inference.inputs
        assert observation.provenance is not None
        pressure = observation.provenance["identity"]["total_pressure_Pa"]
        assert pressure["state"]["value"] == "101325"
        assert pressure["inference"]["relation"] == "extract_inference"
        assert "inferred=true" in pressure["inference"]["inputs"]


def test_mo_fit_links_match_table5_and_pressure_locator_page():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))

    for species in ("MoO2", "MoO3"):
        observations = document["species"][species]["observations"]
        fit = next(
            row
            for row in observations
            if row["observation_id"] == f"oneill_2002_table7_{species.lower()}"
        )
        series = fit["values"]["series"]
        assert {
            row["melt"]: len(row["derived_from"])
            for row in series
        } == TABLE5_MO_COUNTS
        row_inputs = [parent_id for row in series for parent_id in row["derived_from"]]
        assert len(row_inputs) == len(set(row_inputs))
        assert set(fit["derivation"]["inputs"]) == set(row_inputs)

    phase_parents = [
        row
        for species in document["species"].values()
        for row in species["observations"]
        if row.get("type") == "equilibrium_phase_composition"
    ]
    assert len(phase_parents) == 230
    for parent in phase_parents:
        locator = parent["values"]["point_conditions"]["total_pressure_Pa"]["locator"]
        assert (locator["page"], locator["published_page"], locator["pdf_page_index"]) == (
            6,
            156,
            5,
        )
        assert "oil trap (p. 156)" in locator["note"]
        assert "oil trap (p. 156)" in parent["method_note"]
        pressure_provenance = parent["provenance"]["identity"]["total_pressure_Pa"]
        assert any(
            "p. 156 oil-trap vent" in source
            for source in pressure_provenance["inference"]["inputs"]
        )


def test_table7_moo3_values_and_reference_states_are_source_pinned():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_table7_state_source_pins(document)

    mutated = copy.deepcopy(document)
    mutated["species"]["NiO"]["observations"][0]["standard_state"]["state"][
        "value"
    ]["reference_pressure_bar"] = "2"
    with pytest.raises(AssertionError):
        _assert_table7_state_source_pins(mutated)

    migrator = migrate_one_extract(ROOT, EXTRACT, index=None, aliases={})
    migrator.finalize()
    by_formula = {
        formula: [
            observation
            for observation in migrator.result.observations.values()
            if observation.identity.quantity.is_value
            and observation.identity.quantity.value is Quantity.ACTIVITY_COEFFICIENT
            and observation.identity.species is not None
            and observation.identity.species.formula == formula
        ]
        for formula in ("NiO", "CoO", "MoO2", "MoO3")
    }
    assert {formula: len(rows) for formula, rows in by_formula.items()} == {
        "NiO": 19,
        "CoO": 19,
        "MoO2": 17,
        "MoO3": 17,
    }
    for formula in ("NiO", "CoO", "MoO3"):
        for observation in by_formula[formula]:
            reference_state = observation.identity.reference_state
            assert reference_state.is_value
            standard_state = reference_state.value
            assert (
                standard_state.convention
                is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
            )
            assert standard_state.endmember.formula == formula
            assert standard_state.endmember.phase.value is Phase.L
            assert standard_state.component_basis == formula
            assert standard_state.reference_pressure_bar == Decimal("1")
    assert all(
        observation.admission.status is AdmissionStatus.REJECTED
        and "hypothetical" in observation.admission.reason
        and observation.identity.reference_state.is_unknown
        for observation in by_formula["MoO2"]
    )
