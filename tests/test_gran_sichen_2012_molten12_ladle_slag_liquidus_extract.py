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
        if observation["sample_no"] == 4 and observation["phase"] == "liquid"
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

    assert ("MgO", "1", 1, "liquid") in located
    assert ("CaO", "1", 12, "liquid") in located
    assert ("SiO2", "1", 6, "liquid") in located
    assert ("SiO2", "1", 64, "liquid") in located
    assert ("SiO2", "4", 46, "liquid") in located
    assert ("MgO", "4", 46, "MgO") in located
    assert ("CaO", "4", 46, "CaO") in located
    assert ("MgO", "4", 98, "spinel") in located

    contexts = [
        context
        for body in document["species"].values()
        for context in body["context"]
    ]
    activities = [context for context in contexts if context.get("type") == "activity_estimate"]
    figures = [context for context in contexts if context.get("type") == "figure_record"]
    assert len(activities) == 56
    assert all(context["values"]["method_class"] == "model_derived" for context in activities)
    assert len(figures) == 8
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
