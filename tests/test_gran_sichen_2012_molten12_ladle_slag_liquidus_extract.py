"""Extraction and isolated-migration pins for Gran and Sichen (2012)."""

from __future__ import annotations

import yaml

from simulator.battery.migrate import Migrator, REPO_ROOT


EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/gran-sichen-2012-molten12-ladle-slag-liquidus.yaml"
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document: dict) -> list[tuple[str, dict]]:
    return [
        (species, observation)
        for species, body in document["species"].items()
        for observation in body["observations"]
    ]


def test_all_table_compositions_and_source_difference_are_retained() -> None:
    document = _document()
    observations = _observations(document)

    assert len(observations) == 219
    assert {
        table: sum(
            observation["locator"]["table"] == str(table)
            for _species, observation in observations
        )
        for table in (1, 2, 3, 4)
    } == {1: 100, 2: 34, 3: 23, 4: 62}
    assert sum(len(observation["values"]["rows"]) for _, observation in observations) == 876

    sample_4 = {
        observation["locator"]["table"]: observation
        for _, observation in observations
        if observation["sample_no"] == 4 and observation["phase"] == "l"
    }
    assert sample_4["1"]["values"]["composition_mass_percent"]["CaO"] == 49.8
    assert sample_4["2"]["values"]["composition_mass_percent"]["CaO"] == 49.3
    assert sample_4["1"]["assemblage_id"] == sample_4["2"]["assemblage_id"]

    for _species, observation in observations:
        temperature = observation["point_conditions"]["temperature_K"]
        assert temperature["state"]["tag"] == "value"
        assert temperature["state"]["value"] == observation["values"]["T_K"]
        assert temperature["locator"] == observation["locator"]
        assert len(observation["values"]["rows"]) == 4
        assert observation["phase"] == (
            "l" if observation["phase_as_printed"] in {"L", "Liquid"} else "cr"
        )
        assert all(
            row["sample"] == row["analyte"]
            for row in observation["values"]["rows"]
        )
        composition_key = (
            "composition_mass_percent"
            if observation["units"] == "wt%"
            else "composition_mole_percent"
        )
        assert observation["values"][composition_key] == {
            row["analyte"]: row["value"] for row in observation["values"]["rows"]
        }


def test_phase_rows_use_the_ruled_species_containers_and_figures_stay_deferred() -> None:
    document = _document()
    located = {
        (species, observation["locator"]["table"], observation["sample_no"], observation["phase"])
        for species, observation in _observations(document)
    }

    assert ("MgO", "1", 1, "l") in located
    assert ("CaO", "1", 12, "l") in located
    assert ("SiO2", "1", 6, "l") in located
    assert ("SiO2", "1", 64, "l") in located
    assert ("SiO2", "4", 46, "l") in located
    assert ("MgO", "4", 46, "cr") in located
    assert ("CaO", "4", 46, "cr") in located
    assert ("MgO", "4", 98, "cr") in located

    contexts = [
        context
        for body in document["species"].values()
        for context in body["context"]
    ]
    activities = [context for context in contexts if context.get("type") == "activity_estimate"]
    figures = [context for context in contexts if context.get("type") == "figure_record"]
    assert len(activities) == 60
    assert all(context["values"]["method_class"] == "model_derived" for context in activities)
    assert len(figures) == 8
    assert all(context["units"] == "as printed" for context in figures)
    assert all(
        context["digitisation"] == "deferred to a figure pass"
        and "points" not in context
        and "caption" in context["values"]
        for context in figures
    )
    assert all(
        "marker_count" in series
        for context in figures
        for series in context["series"]
    )


def test_table4_locators_match_the_printed_page_break() -> None:
    document = _document()
    observations = {
        observation["observation_id"]: observation
        for _, observation in _observations(document)
    }
    contexts = {
        context["observation_id"]: context
        for body in document["species"].values()
        for context in body["context"]
        if context.get("observation_id")
    }

    expected_pages = {
        **{
            f"gran_2012_t4_s{sample:03}_{phase}": 11
            for sample in (87, 88, 89, 90)
            for phase in ("liquid", "mgo")
        },
        **{
            f"gran_2012_t4_s{sample:03}_{phase}": 12
            for sample in (46, 48)
            for phase in ("liquid", "mgo", "cao")
        },
        **{f"gran_2012_t4_s{sample:03}_activity": 11 for sample in (87, 88, 89, 90)},
        **{f"gran_2012_t4_s{sample:03}_activity": 12 for sample in (46, 48)},
    }
    for observation_id, expected_page in expected_pages.items():
        record = observations.get(observation_id, contexts.get(observation_id))
        assert record is not None
        assert record["locator"]["pdf_page_index"] == expected_page
        if observation_id in observations:
            assert (
                record["point_conditions"]["temperature_K"]["locator"]["pdf_page_index"]
                == expected_page
            )


def test_model_derived_prose_values_are_explicitly_parked() -> None:
    document = _document()
    method = document["extraction"]["method"]
    for value in ("47,900", "49,500", "77,800", "0.0402", "−20,790 − 15.7T"):
        assert value in method
    assert all(value in method for value in ("1873 K", "1773 K", "[9]"))
    method_context = next(
        row
        for row in document["species"]["SiO2"]["context"]
        if row.get("observation_id") == "gran_2012_experimental_method"
    )
    assert method_context["units"] == "as printed"
    assert "About 0.5 g oxide charges" in method_context["values"]["statement_as_printed"]

    prose_activities = {
        context["observation_id"]: context
        for context in document["species"]["SiO2"]["context"]
        if context.get("observation_id", "").startswith("gran_2012_prose_")
    }
    expected = {
        "gran_2012_prose_a_Al2O3_1873": ("a_Al2O3", 1873, "0.0402", False),
        "gran_2012_prose_a_AlO1_5_1873": ("a_AlO1.5", 1873, "0.201", True),
        "gran_2012_prose_f_AlO1_5_1873": ("f_AlO1.5", 1873, "86", True),
        "gran_2012_prose_f_AlO1_5_1773": ("f_AlO1.5", 1773, "188", True),
    }
    assert set(prose_activities) == set(expected)
    for observation_id, (quantity, temperature, value, approximate) in expected.items():
        context = prose_activities[observation_id]
        assert context["type"] == "activity_estimate"
        assert context["units"] == "dimensionless"
        assert context["temperature_K"] == temperature
        assert context["locator"] == {
            "page": 9,
            "published_page": 9,
            "pdf_page_index": 8,
        }
        assert context["values"]["method_class"] == "model_derived"
        assert context["values"]["admission_status"] == "parked"
        assert context["values"]["quantity_as_published"] == quantity
        assert context["values"]["value_as_printed"] == value
        assert context["values"].get("approximate", False) is approximate


def test_isolated_migration_has_no_hard_issues_and_preserves_every_temperature() -> None:
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    observations = list(result.observations.values())
    assert len(observations) == 876
    assert not result.validation.hard_issues
    assert all("temperature_K" in observation.point_conditions for observation in observations)
    for observation in observations:
        condition = observation.point_conditions["temperature_K"]
        assert condition.state.is_value
        assert condition.locator is not None
        assert observation.identity.temperature_K.is_value
        assert condition.state.value == observation.identity.temperature_K.value
    calcium_point = next(
        observation
        for observation in observations
        if str(observation.value.point) == "49.8"
    )
    assert calcium_point.identity.species.formula == "CaO"
    assert calcium_point.identity.species.phase.is_value
    assert calcium_point.identity.species.phase.value.value == "l"
