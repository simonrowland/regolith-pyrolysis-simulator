from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import re

import pytest
import yaml

from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "fortin-2014-sulfur-saturation-natural-melts-thesis"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"


def _observations(document: dict, species: str) -> list[dict]:
    return document["species"][species]["observations"]


def _table3_row(document: dict, species: str, sample: str) -> dict:
    matches = [
        row
        for row in _observations(document, species)
        if row.get("observation_id", "").startswith("fortin_2014_table3_")
        and row.get("values", {}).get("sample_id_as_published") == sample
    ]
    assert len(matches) == 1
    return matches[0]


def _appendix_row(document: dict, appendix: str, sample: str) -> dict:
    matches = [
        row
        for row in _observations(document, "S")
        if row.get("observation_id", "").startswith(f"fortin_2014_{appendix}_")
        and row.get("values", {}).get("sample_id_as_published") == sample
    ]
    assert len(matches) == 1
    return matches[0]


def _all_observations(document: dict) -> list[dict]:
    return [
        row
        for species in document["species"].values()
        for row in species.get("observations", [])
    ]


def _assert_render_pins(document: dict) -> None:
    # Values and locators below were independently checked against the page renders.
    pins = (
        (_table3_row(document, "S", "MAF-1"), "874", "874 (48)", 38, "3", "S"),
        (_table3_row(document, "S", "MAF-8"), "943", "943 (34)", 38, "3", "S"),
        (_table3_row(document, "SiO2", "MAF-1"), "47.7", "47.7 (3)", 38, "3", "SiO2"),
    )
    for row, value, printed, page, table, column in pins:
        assert str(row["values"]["value"]) == value
        assert row["values"]["value_as_published"] == printed
        assert row["locator"]["page"] == page
        assert row["locator"]["table"] == table
        assert row["locator"]["row"] == row["values"]["sample_id_as_published"]
        assert row["locator"]["column"] == column

    appendix_pins = (
        ("appendixi", "MAF-45", "1265", 83, "Appendix I", "S ppm"),
        ("appendixii", "B-16", "1210", 92, "Appendix II", "S ppm"),
        ("appendixiii", "MP15X", "126", 93, "Appendix III", "S ppm"),
    )
    for appendix, sample, value, page, table, column in appendix_pins:
        row = _appendix_row(document, appendix, sample)
        assert str(row["values"]["value"]) == value
        assert row["locator"]["page"] == page
        assert row["locator"]["table"] == table
        assert row["locator"]["row"].startswith(sample)
        assert row["locator"]["column"] == column


def test_render_fidelity_pins_and_mutation_check() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_render_pins(document)

    mutated = deepcopy(document)
    _table3_row(mutated, "S", "MAF-1")["values"]["value"] = "875"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)


def test_appendix_iii_attribution_tokenization_matches_rendered_rows() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = [
        row for row in _observations(document, "S")
        if row.get("observation_id", "").startswith("fortin_2014_appendixiii_")
    ]
    assert len(rows) == 108
    assert Counter(str(row["values"]["source_number_as_printed"]) for row in rows) == {
        "1": 4, "2": 53, "3": 15, "4": 10, "5": 21, "6": 5,
    }
    for row in rows:
        values = row["values"]
        printed = values["row_as_published"]
        assert values["sample_id_as_published"] == str(printed["run#"])
        assert values["source_number_as_printed"] == str(printed["Source"])
        assert values["method_as_printed"] == printed["water method"]

    luhr_runs = {
        "198", "161", "223", "185", "163", "260", "200", "286", "275", "148", "144",
        "218", "187", "230", "156", "146", "220", "225", "153", "228", "155",
    }
    luhr = [
        row for row in rows
        if row["values"]["source_number_as_printed"] == "5"
        and row["values"]["sample_id_as_published"] in luhr_runs
    ]
    assert len(luhr) == 21
    assert all(row["values"]["source_number_as_printed"] == "5" for row in luhr)
    assert all(row["values"]["method_as_printed"] == "albite-H2O Burnham" for row in luhr)
    assert all(row["values"]["attribution_doi"] == "10.1093/petrology/31.5.1071" for row in luhr)

    zajacz = next(
        row for row in rows
        if row["values"]["source_number_as_printed"] == "6"
        and row["values"]["sample_id_as_published"] == "196"
    )
    assert zajacz["values"]["source_number_as_printed"] == "6"
    assert zajacz["values"]["sample_id_as_published"] == "196"
    assert zajacz["values"]["method_as_printed"] == "albite-H2O Burnham"
    assert zajacz["values"]["attribution_doi"] == "10.1016/j.gca.2013.02.026"


def test_below_detection_rows_keep_literal_and_table3_conditions() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    below = [
        row for row in _all_observations(document)
        if row.get("observation_id", "").startswith("fortin_2014_table3_")
        and row.get("values", {}).get("admission_status") == "unavailable"
    ]
    assert len(below) == 18
    for row in below:
        assert row["values"]["value_as_published"] == "<d.l."
        assert "point_conditions" not in row["values"]
        conditions = row["point_conditions"]
        assert conditions["temperature_K"]["state"]["tag"] == "value"
        assert conditions["composition"]["state"]["tag"] == "value"
        pressure = conditions["total_pressure_Pa"]
        assert pressure["state"]["value"] == 1_000_000_000
        assert pressure["units"] == "Pa"
        assert "Printed as 1 GPa" in pressure["note"]


def test_printed_rows_identity_supersession_uncertainty_and_migration() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_render_pins(document)
    below = [
        row for row in _all_observations(document)
        if row.get("observation_id", "").startswith("fortin_2014_table3_")
        and row.get("values", {}).get("admission_status") == "unavailable"
    ]

    table3_s = [
        row for row in _observations(document, "S")
        if row.get("observation_id", "").startswith("fortin_2014_table3_s_")
        and row.get("values", {}).get("quantity") == "equilibrium_phase_composition"
    ]
    assert len(table3_s) == 27
    assert len({row["values"]["sample_id_as_published"] for row in table3_s}) == 27
    expected_rows = {"appendixi": 238, "appendixii": 14, "appendixiii": 108}
    for appendix, expected in expected_rows.items():
        rows = [
            row for row in _observations(document, "S")
            if row.get("observation_id", "").startswith(f"fortin_2014_{appendix}_")
        ]
        assert len(rows) == expected
        assert all("point_conditions" in row and "point_conditions" not in row["values"] for row in rows)

    # The dash-only dissolved-water cell is not promoted to a numeric observation.
    assert not any(
        row.get("values", {}).get("sample_id_as_published") == "MAF-29"
        for row in _all_observations(document)
        if row.get("observation_id", "").startswith("fortin_2014_table3_h2o_")
    )

    appendix_samples = (
        ("appendixi", "MAF-38", Decimal("1523"), Decimal("1000000000")),
        ("appendixii", "B-16", Decimal("1323"), Decimal("299000000")),
        ("appendixiii", "S18", Decimal("1473"), Decimal("200000000")),
    )
    for appendix, sample, temperature, pressure in appendix_samples:
        row = _appendix_row(document, appendix, sample)
        conditions = row["point_conditions"]
        assert conditions["temperature_K"]["state"]["value"] == str(temperature)
        assert conditions["total_pressure_Pa"]["state"]["value"] == pressure
        assert conditions["composition"]["state"]["tag"] == "value"
        assert row["point_conditions"]["composition"]["locator"]["table"] == {
            "appendixi": "Appendix I", "appendixii": "Appendix II", "appendixiii": "Appendix III"
        }[appendix]

    slope = next(
        row for row in _observations(document, "S")
        if row.get("observation_id") == "fortin_2014_average_water_scss_slope"
    )
    assert slope["values"]["value_as_published"] == "93±4"
    assert slope["uncertainty"]["value"] == "4"
    assert slope["uncertainty"]["units"] == "ppm S/wt.% H2O"
    assert slope["uncertainty"]["confidence_level"] == "not_stated"
    assert slope["derivation"]["raw_inputs"]["state"]["value"] == "not_printed"
    context = document["species"]["S"]["context"]
    temperature = next(row for row in context if row["observation_id"] == "fortin_2014_temperature_control_precision")
    standard = next(row for row in context if row["observation_id"] == "fortin_2014_vg2_sulfur_standard")
    assert temperature["uncertainty"]["value_as_published"] == "1250±2 °C"
    assert temperature["uncertainty"]["value"] == "2"
    assert temperature["uncertainty"]["units"] == "°C"
    assert standard["uncertainty"]["value_as_published"] == "1402±28 ppm"
    assert standard["uncertainty"]["value"] == "28"
    assert standard["uncertainty"]["units"] == "ppm"

    superseding_rows = [row for row in table3_s if row.get("supersedes")]
    assert len(superseding_rows) == 18
    assert all(row["supersedes"].startswith("fortin_2014_appendixi_source1_row_") for row in superseding_rows)
    appendix_by_id = {
        row["observation_id"]: row
        for row in _observations(document, "S")
        if row.get("observation_id", "").startswith("fortin_2014_appendixi_source1_")
    }
    for row in superseding_rows:
        duplicate = appendix_by_id[row["supersedes"]]
        assert row["values"]["sample_id_as_published"] == duplicate["values"]["sample_id_as_published"]

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    migrated = list(result.observations.values())
    assert not result.validation.hard_issues
    assert len(migrated) == 688
    assert len(result.experiments) == 28
    assert Counter(row.value.kind.value for row in migrated) == Counter({"point": 665, "unavailable": 23})
    assert Counter(row.uncertainty.kind.value for row in migrated) == Counter({"printed": 670, "none": 18})
    uncertainty_confidence = Counter(
        (
            row.uncertainty.verbatim.get("confidence_level")
            or "confidence_level_not_stated"
        )
        if isinstance(row.uncertainty.verbatim, dict)
        else "confidence_level_not_stated"
        for row in migrated
        if row.uncertainty.kind.value == "printed"
    )
    assert uncertainty_confidence == Counter({
        "one_sigma": 307,
        "confidence_level_not_stated": 362,
        "not_stated": 1,
    })

    by_id = {row.observation_id.split("::", 1)[1]: row for row in migrated}
    for row in superseding_rows:
        winner = by_id[row["observation_id"]]
        superseded = by_id[row["supersedes"]]
        assert winner.admission.status.value == "admitted"
        assert superseded.admission.status.value == "superseded"
        assert superseded.admission.superseded_by == winner.observation_id
        assert winner.value.point == superseded.value.point

    for appendix, sample, _temperature, _pressure in appendix_samples:
        source = _appendix_row(document, appendix, sample)
        row = by_id[source["observation_id"]]
        assert row.identity.temperature_K.is_value
        assert row.identity.total_pressure_Pa.is_value
        assert row.identity.composition.is_value
        assert row.identity.species.phase.is_value
        assert row.identity.species.phase.value.value == "glass"
        assert row.point_conditions["temperature_K"].state.is_value
        assert row.point_conditions["total_pressure_Pa"].state.is_value
        assert row.point_conditions["composition"].state.is_value

    unavailable_ids = {row["observation_id"] for row in below}
    for source_id in unavailable_ids:
        row = by_id[source_id]
        assert row.value.kind.value == "unavailable"
        assert row.identity.temperature_K.is_value
        assert row.identity.total_pressure_Pa.is_value
        assert row.identity.total_pressure_Pa.value == Decimal("1000000000")
        assert row.identity.composition.is_value

    quantity_unknown = sorted(row.observation_id for row in migrated if not row.identity.quantity.is_value)
    assert quantity_unknown == sorted([
        f"{SOURCE_ID}::{observation_id}"
        for observation_id in (
            "fortin_2014_table4_equation_8_scss_model_coefficients",
            "fortin_2014_table4_equation_10_scss_model_coefficients",
            "fortin_2014_equation7_liu_comparator",
            "fortin_2014_equation9_li_ripley_comparator",
            "fortin_2014_average_water_scss_slope",
        )
    ])
    assert not [row.observation_id for row in migrated if not row.identity.species.phase.is_value]

    # The reported slope uncertainty is retained on the migrated observation.
    slope_row = by_id["fortin_2014_average_water_scss_slope"]
    assert slope_row.uncertainty.kind.value == "printed"
    assert slope_row.uncertainty.verbatim["value"] == "4"
    assert slope_row.uncertainty.verbatim["units"] == "ppm S/wt.% H2O"
    assert slope_row.uncertainty.verbatim["confidence_level"] == "not_stated"
