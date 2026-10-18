"""Quoted-property, deferred-figure, and isolated-migration pins for Connolly (2016)."""

from __future__ import annotations

import yaml

from simulator.battery.enums import PerBasis, ValueKind
from simulator.battery.migrate import Migrator, REPO_ROOT


EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/connolly-2016-si-o-liquid-vapor-caloric-vdw.yaml"
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_kraus_quoted_properties_and_shock_estimates_match_rendered_cells() -> None:
    document = _document()
    observations = document["species"]["SiO2"]["observations"]
    assert len(observations) == 2

    by_quantity = {row["values"]["quantity"]: row for row in observations}
    entropy = by_quantity["S"]
    heat_capacity = by_quantity["cp"]
    assert entropy["values"]["entropy"] == 172.17
    assert entropy["values"]["value_as_printed"] == "172.17 ± 3.9"
    assert entropy["values"]["uncertainty"] == 3.9
    assert heat_capacity["values"]["cp"] == 83.25
    assert heat_capacity["values"]["value_as_printed"] == "83.25 ± 4.1"
    assert heat_capacity["values"]["uncertainty"] == 4.1
    for row in observations:
        assert row["values"]["method_class"] == "quoted_attributed"
        assert row["values"]["attribution"] == "Kraus et al. [2012]"
        assert row["phase_as_printed"] == "SiO2(l)"
        assert row["units_as_printed"] == "J/mol-K"
        assert row["point_conditions"]["temperature_K"]["state"] == {
            "tag": "value",
            "value": 1999,
        }
        assert row["locator"]["pdf_page_index"] == 23

    shock = next(
        context
        for context in document["species"]["SiO2"]["context"]
        if context.get("observation_id") == "connolly_2016_kraus_table3_constraint"
    )
    assert shock["values"]["values_as_printed"] == {
        "Tb": "3,177 ± 115 K",
        "s_b,l": "3,552 ± 70 J/K-kg",
        "s_b,g": "7,254 ± 8 J/K-kg",
        "Tc": "<4,961 ± 559 K",
        "s_c": "<5,798 ± 277 J/K-kg",
    }
    assert shock["locator"]["pdf_page_index"] == 12


def test_all_seven_figures_are_deferred_context_records_without_points() -> None:
    document = _document()
    contexts = document["species"]["SiO2"]["context"]
    figures = [row for row in contexts if row.get("type") == "figure_record"]

    assert len(figures) == 7
    assert {row["locator"]["figure"] for row in figures} == set("1234567")
    assert all(row["values"]["method_class"] == "model_derived" for row in figures)
    assert all(row["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all("points" not in row and "numeric_points" not in row for row in figures)
    assert {
        row["locator"]["figure"]: row["locator"]["pdf_page_index"]
        for row in figures
    } == {"1": 9, "2": 11, "3": 13, "4": 14, "5": 15, "6": 16, "7": 18}
    assert all(
        isinstance(series["marker_count"], int)
        for row in figures
        for series in row["series"]
    )


def test_isolated_migration_preserves_both_quoted_temperature_identities() -> None:
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    observations = list(result.observations.values())
    assert len(observations) == 2
    assert not result.validation.hard_issues
    assert {observation.identity.temperature_K.value for observation in observations} == {1999}
    assert all("temperature_K" in observation.point_conditions for observation in observations)
    assert all(observation.value.kind is ValueKind.POINT for observation in observations)
    assert all(observation.identity.species.phase.is_value for observation in observations)
    assert all(
        observation.identity.per.value is PerBasis.MOL_SPECIES
        for observation in observations
    )
    assert all(
        not observation.identity.standard_pressure_Pa.is_value
        for observation in observations
    )
    assert all(
        observation.point_conditions["temperature_K"].state.is_value
        and observation.point_conditions["temperature_K"].locator is not None
        for observation in observations
    )
    assert len(result.queue) == 2
    assert all(entry.axes == ["method"] for entry in result.queue)
    assert all(entry.why == "source does not state method" for entry in result.queue)
