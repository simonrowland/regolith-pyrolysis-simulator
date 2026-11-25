"""Pins Chen et al. (2022) Table II readings and condition migration."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase
from simulator.battery.migrate import wt_pct_to_mole_fraction
from tests.battery.test_migrate import _migrate_real_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT_NAME = "chen-2022-mgo-sio2-tio2-phase-equilibria.yaml"
EXTRACT = ROOT / "data" / "literature" / "extracts" / EXTRACT_NAME
SINGLE_LIQUID_COMPOSITIONS = {
    "chen22_C1": [("TiO2", "45.9"), ("SiO2", "33.5"), ("MgO", "20.5")],
    "chen22_C68": [("TiO2", "10.2"), ("SiO2", "57.0"), ("MgO", "32.7")],
    "chen22_C69": [("TiO2", "12.8"), ("SiO2", "53.9"), ("MgO", "33.3")],
    "chen22_C70": [("TiO2", "15.3"), ("SiO2", "51.5"), ("MgO", "33.2")],
    "chen22_C71": [("TiO2", "17.8"), ("SiO2", "49.0"), ("MgO", "33.2")],
    "chen22_C75": [("TiO2", "37.8"), ("SiO2", "35.8"), ("MgO", "26.4")],
}
SINGLE_LIQUID_CHARGES = {
    "chen22_C1": [("TiO2", "44.92"), ("SiO2", "33.23"), ("MgO", "21.84")],
    "chen22_C68": [("TiO2", "10.07"), ("SiO2", "55.81"), ("MgO", "34.12")],
    "chen22_C69": [("TiO2", "12.47"), ("SiO2", "52.91"), ("MgO", "34.62")],
    "chen22_C70": [("TiO2", "15.2"), ("SiO2", "50.15"), ("MgO", "34.65")],
    "chen22_C71": [("TiO2", "17.7"), ("SiO2", "47.79"), ("MgO", "34.51")],
    "chen22_C75": [("TiO2", "38.03"), ("SiO2", "35.11"), ("MgO", "26.86")],
}


def _observations_by_id(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species["observations"]
    }


def _context_by_id(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("context", [])
        if "observation_id" in row
    }


def _assert_observation_species_matches_component(document: dict) -> None:
    for formula, block in document["species"].items():
        for row in block["observations"]:
            assert row["values"]["component_as_published"] == formula


def _assert_single_liquid_run_compositions(document: dict) -> None:
    experiments = {row["experiment_id"]: row for row in document["experiments"]}
    contexts = _context_by_id(document)
    moved = {
        row["observation_id"]: row
        for row in contexts.values()
        if row.get("experiment") in SINGLE_LIQUID_COMPOSITIONS
        and row.get("values", {}).get("quantity")
        == "equilibrium_phase_composition"
    }
    assert len(moved) == 18

    for experiment_id, expected in SINGLE_LIQUID_COMPOSITIONS.items():
        experiment = experiments[experiment_id]
        sample = experiment["sample"]
        assert sample["initial_composition"]["state"]["value"]["components"] == [
            list(component) for component in expected
        ]
        assert sample["printed_composition"]["state"]["value"]["components"] == [
            list(component) for component in SINGLE_LIQUID_CHARGES[experiment_id]
        ]
        run_rows = [row for row in moved.values() if row["experiment"] == experiment_id]
        assert len(run_rows) == 3
        assert {
            row["values"]["component_as_published"]: Decimal(
                str(row["values"]["value"])
            )
            for row in run_rows
        } == {formula: Decimal(amount) for formula, amount in expected}
        for row in run_rows:
            assert row["type"] == "phase_composition_context"
            assert row["point_conditions"]["assemblage_as_published"]["state"][
                "value"
            ] == "Single liquid"

    expected_ids = {
        f"{experiment_id}_liquid_{formula.lower()}"
        for experiment_id, components in SINGLE_LIQUID_COMPOSITIONS.items()
        for formula, _amount in components
    }
    assert set(moved) == expected_ids


def test_single_liquid_compositions_move_to_run_conditions_and_reject_mutations() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_single_liquid_run_compositions(document)

    changed_run = deepcopy(document)
    sample = next(
        row["sample"]
        for row in changed_run["experiments"]
        if row["experiment_id"] == "chen22_C1"
    )
    sample["initial_composition"]["state"]["value"]["components"][0][1] = "46.0"
    with pytest.raises(AssertionError):
        _assert_single_liquid_run_compositions(changed_run)

    changed_analysis = deepcopy(document)
    contexts = _context_by_id(changed_analysis)
    contexts["chen22_C1_liquid_tio2"]["values"]["value"] = 46.0
    with pytest.raises(AssertionError):
        _assert_single_liquid_run_compositions(changed_analysis)


def test_table_ii_render_pins_and_complete_row_migration(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = _observations_by_id(document)
    extract_text = EXTRACT.read_text(encoding="utf-8")
    assert extract_text.count("review_status: draft") == 1
    assert document["review_status"] == "draft"
    assert document["source"]["citation"].startswith(
        "Chen, Min, Wan, Xingbang, Shi, Junjie, Taskinen, Pekka, and Jokilaakso, Ari"
    )

    # Table II, 600 dpi renders: pp. 678-679 (PDF pages 3-4; indexes 2-3).
    pins = {
        "chen22_C52_liquid_sio2": (58.0, 678, 2, "C52, Liquid"),
        "chen22_C53_cristobalite_tio2": (3.6, 678, 2, "C53, Cristobalite"),
        "chen22_C66_mgo_2tio2_mgo": (20.0, 678, 2, "C66, MgO·2TiO2"),
    }
    for observation_id, (value, folio, page_index, row) in pins.items():
        observation = rows[observation_id]
        assert observation["values"]["value"] == value
        assert observation["locator"]["published_page"] == folio
        assert observation["locator"]["pdf_page_index"] == page_index
        assert observation["locator"]["row"] == row

    _assert_single_liquid_run_compositions(document)
    assert sum(len(species["observations"]) for species in document["species"].values()) == 180
    assert all(
        row["phase"] in {"l", "cr"}
        for species in document["species"].values()
        for row in species["observations"]
    )
    solid_names = {
        "Cristobalite",
        "TiO2",
        "MgO·SiO2",
        "2MgO·SiO2",
        "MgO·2TiO2",
    }
    assert {
        row["condensed_form"]["polymorph"]
        for species in document["species"].values()
        for row in species["observations"]
        if row["phase"] == "cr"
    } == solid_names
    assert all(
        "condensed_form" not in row
        for species in document["species"].values()
        for row in species["observations"]
        if row["phase"] == "l"
    )
    assert {
        container: len(block["observations"])
        for container, block in document["species"].items()
    } == {
        "SiO2": 60,
        "MgO": 60,
        "TiO2": 60,
    }
    _assert_observation_species_matches_component(document)
    changed_species = deepcopy(document)
    wrong_group = changed_species["species"]["TiO2"]["observations"]
    moved = next(
        row
        for row in wrong_group
        if row["observation_id"] == "chen22_C7_liquid_tio2"
    )
    wrong_group.remove(moved)
    changed_species["species"]["SiO2"]["observations"].append(moved)
    with pytest.raises(AssertionError):
        _assert_observation_species_matches_component(changed_species)
    containers = {
        observation["observation_id"]: container
        for container, block in document["species"].items()
        for observation in block["observations"]
    }
    expected_containers = {
        "chen22_C5_liquid_tio2": "TiO2",  # liquid with one coexisting solid
        "chen22_C73_liquid_tio2": "TiO2",  # liquid with one coexisting MgO·SiO2 solid
        "chen22_C7_liquid_tio2": "TiO2",  # liquid in a three-phase invariant
        "chen22_C45_liquid_tio2": "TiO2",  # liquid with two solids
        "chen22_C65_mgo_2tio2_sio2": "SiO2",  # solid row's reported component
        "chen22_C58_2mgo_sio2_mgo": "MgO",
    }
    assert {key: containers[key] for key in expected_containers} == expected_containers
    assert len(
        [
            observation
            for observation in rows.values()
            if isinstance(observation["values"]["value"], str)
        ]
    ) == 11

    figures = [
        row
        for row in document["species"]["SiO2"]["context"]
        if row["type"] == "figure_record"
    ]
    assert len(figures) == 6
    assert all(
        figure["values"]["digitisation"] == "deferred to a figure pass"
        and figure["values"]["numeric_points"] == "none"
        for figure in figures
    )

    result = _migrate_real_extract(tmp_path, EXTRACT_NAME)
    assert len(result.observations) == 180
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert sum(row.value.kind.value == "point" for row in result.observations.values()) == 169
    assert sum(row.value.kind.value == "unavailable" for row in result.observations.values()) == 11

    migrated_components = {
        row["observation_id"]: row["values"]["component_as_published"]
        for row in rows.values()
    }
    assemblages_by_sample: dict[str, set[str]] = {}
    for observation_id, migrated in result.observations.items():
        source_observation_id = observation_id.split("::", 1)[-1]
        source_phase = rows[source_observation_id]["phase"]
        expected_phase = Phase.L if source_phase == "l" else Phase.CR
        assert migrated.identity.species.phase.value is expected_phase
        conditions = migrated.point_conditions or {}
        assert (
            migrated.identity.species.formula
            == migrated_components[source_observation_id]
        )
        temperature = conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.locator is not None
        assert temperature.state.value == Decimal("1773.15")
        for key in (
            "sample_id",
            "assemblage_id",
            "assemblage_as_published",
            "system_as_published",
            "atmosphere",
            "initial_composition_as_published",
            "phase_composition_as_published",
        ):
            assert key in conditions
            assert conditions[key].state.is_value
            assert conditions[key].locator is not None
        sample_id = conditions["sample_id"].state.value
        assemblage_id = conditions["assemblage_id"].state.value
        assert assemblage_id == f"chen22_{sample_id}"
        assemblages_by_sample.setdefault(sample_id, set()).add(assemblage_id)
        assert set(conditions["phase_composition_as_published"].state.value) == {
            "TiO2",
            "SiO2",
            "MgO",
        }
        assert set(conditions["initial_composition_as_published"].state.value) == {
            "TiO2",
            "SiO2",
            "MgO",
        }
    assert len(assemblages_by_sample) == 26
    assert all(len(assemblage_ids) == 1 for assemblage_ids in assemblages_by_sample.values())

    work_id = result.aliases[document["source_id"]]
    for experiment_id in SINGLE_LIQUID_COMPOSITIONS:
        migrated_experiment = result.experiments[
            f"{work_id}::experiment::{experiment_id}"
        ]
        assert migrated_experiment.sample.initial_composition is not None
        assert migrated_experiment.sample.initial_composition.state.is_value
        assert migrated_experiment.sample.initial_composition.state.value.components == (
            wt_pct_to_mole_fraction(
                {
                    formula: Decimal(amount)
                    for formula, amount in SINGLE_LIQUID_COMPOSITIONS[experiment_id]
                }
            ).components
        )
        assert migrated_experiment.sample.initial_composition.locator is not None
        assert migrated_experiment.sample.initial_composition.locator.pdf_page_index == 2
        assert migrated_experiment.sample.initial_composition.locator.record == (
            f"{experiment_id.removeprefix('chen22_')}, Liquid"
        )
        assert migrated_experiment.sample.printed_composition is not None
        assert migrated_experiment.sample.printed_composition.state.is_value
        assert not any(
            observation.experiment_id.endswith(f"::experiment::{experiment_id}")
            for observation in result.observations.values()
        )

    method = next(
        row
        for row in document["species"]["SiO2"]["context"]
        if row["observation_id"] == "chen22_table_ii_experimental_method"
    )
    method_values = method["values"]
    assert method_values["log10_P_O2_atm_as_published"] == -0.6778
    assert method_values["log10_P_O2_locator"]["pdf_page_index"] == 3
    assert method_values["total_pressure_atm_as_published"] == 1
    assert method_values["total_pressure_text_as_published"] == "P = 1 atm"
    assert method_values["total_pressure_locator"]["pdf_page_index"] == 9

    table_i = next(
        row
        for row in document["species"]["SiO2"]["context"]
        if row["observation_id"] == "chen22_table_i_reagent_purities"
    )
    assert table_i["locator"]["published_page"] == 677
    assert table_i["locator"]["pdf_page_index"] == 1
    assert [
        (row["oxide"], row["purity"], row["supplier"], row["location"])
        for row in table_i["values"]["materials"]
    ] == [
        ("SiO2", 99.995, "Alfa Aesar", "Kandel"),
        ("MgO", 99.95, "Alfa Aesar", "Karlsruhe"),
        ("TiO2", 99.8, "Sigma-Aldrich", "Steinheim"),
    ]
